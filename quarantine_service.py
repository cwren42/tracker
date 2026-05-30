"""
quarantine_service.py — Exchange Online quarantine integration via Microsoft Defender API.

Required Azure App permissions (all Application type, grant admin consent):
  - ThreatHunting.Read.All        — Advanced Hunting KQL (list/sync)
  - SecurityEvents.ReadWrite.All  — Remediation actions (release/delete)

No Exchange PS RBAC role assignment required.
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# ── Microsoft API endpoints ───────────────────────────────────────────────────
_GRAPH_BASE   = "https://graph.microsoft.com/v1.0"
_SECURITY_API = "https://api.security.microsoft.com"


class QuarantineService:
    """Pull quarantine messages from Exchange Online and extract threat intel."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._tokens: dict[str, dict] = {}  # scope -> {access_token, expiry}

    # ── Token management ─────────────────────────────────────────────────────

    def _get_token(self, scope: str) -> str:
        cached = self._tokens.get(scope)
        if cached and datetime.utcnow() < cached["expiry"]:
            return cached["access_token"]

        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        resp = requests.post(url, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": scope,
            "grant_type": "client_credentials",
        }, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600)) - 300
        self._tokens[scope] = {
            "access_token": token,
            "expiry": datetime.utcnow() + timedelta(seconds=expires_in),
        }
        return token

    def _graph_token(self) -> str:
        return self._get_token("https://graph.microsoft.com/.default")

    def _security_token(self) -> str:
        return self._get_token("https://api.security.microsoft.com/.default")

    def _graph_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._graph_token()}", "Content-Type": "application/json"}

    @staticmethod
    def _normalize_internet_message_id(internet_message_id: str) -> str:
        iid = (internet_message_id or "").strip()
        if iid and not iid.startswith("<"):
            iid = f"<{iid}>"
        if iid and not iid.endswith(">"):
            iid = f"{iid}>"
        return iid

    def _security_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._security_token()}", "Content-Type": "application/json"}

    # ── Core: Advanced Hunting via Defender 365 ───────────────────────────────

    @staticmethod
    def _format_hunting_datetime(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.isoformat().replace("+00:00", "Z")

    def get_quarantine_messages_via_hunting(
        self,
        days: int = 30,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        recipient_address: str | None = None,
        limit: int = 10000,
    ) -> list[dict]:
        """
        Pull ALL email events via Advanced Hunting KQL for full Barracuda-style visibility.
        Captures: Delivered, Junk, Quarantined, Blocked, and Released messages.
        Returns a list of normalized message dicts, deduped on NetworkMessageId.
        """
        filter_lines = []
        if start_time:
            filter_lines.append(
                f"| where Timestamp >= datetime({self._format_hunting_datetime(start_time)})"
            )
        else:
            filter_lines.append(f"| where Timestamp >= ago({days}d)")
        if end_time:
            filter_lines.append(
                f"| where Timestamp <= datetime({self._format_hunting_datetime(end_time)})"
            )
        if recipient_address:
            recipient_escaped = recipient_address.replace('"', '\\"').lower()
            filter_lines.append(f'| where RecipientEmailAddress =~ "{recipient_escaped}"')

        kql = f"""
EmailEvents
{'\n'.join(filter_lines)}
| project
    NetworkMessageId,
    InternetMessageId,
    SenderFromAddress,
    SenderDisplayName,
    SenderMailFromDomain,
    RecipientEmailAddress,
    Subject,
    Timestamp,
    ThreatTypes,
    ThreatNames,
    DetectionMethods,
    EmailDirection,
    DeliveryAction,
    DeliveryLocation,
    LatestDeliveryAction,
    LatestDeliveryLocation,
    OrgLevelPolicy,
    UserLevelPolicy,
    BulkComplaintLevel,
    AuthenticationDetails,
    UrlCount,
    AttachmentCount,
    SenderIPv4,
    SenderIPv6
| order by Timestamp desc
| take {max(limit, 1)}
"""
        try:
            resp = requests.post(
                f"{_SECURITY_API}/api/advancedhunting/run",
                headers=self._security_headers(),
                json={"Query": kql},
                timeout=120,
            )
            resp.raise_for_status()
            results = resp.json().get("Results", [])
            logger.info("Advanced Hunting returned %d raw rows", len(results))
            # Deduplicate: one row per NetworkMessageId (keep most recent)
            seen: dict[str, dict] = {}
            for r in results:
                mid = r.get("NetworkMessageId", "")
                if mid and mid not in seen:
                    seen[mid] = r
            deduped = list(seen.values())
            logger.info("After dedup: %d unique messages", len(deduped))
            return [self._normalize_hunting_row(r) for r in deduped]
        except requests.HTTPError as e:
            logger.error("Advanced Hunting failed: %s — %s", e, e.response.text if e.response else "")
            return []
        except Exception as e:
            logger.error("Advanced Hunting error: %s", e)
            return []

    def _normalize_hunting_row(self, row: dict) -> dict:
        auth_raw = row.get("AuthenticationDetails", "") or ""
        spf = self._extract_auth(auth_raw, "spf")
        dkim = self._extract_auth(auth_raw, "dkim")
        dmarc = self._extract_auth(auth_raw, "dmarc")

        threat_types = row.get("ThreatTypes", "") or ""
        # Normalize to a single primary threat label
        if "Phish" in threat_types:
            threat_type = "Phish"
        elif "Malware" in threat_types:
            threat_type = "Malware"
        elif "Spam" in threat_types:
            threat_type = "Spam"
        else:
            threat_type = "Bulk" if row.get("BulkComplaintLevel", 0) else "Unknown"

        sender = (row.get("SenderFromAddress") or "").lower()
        domain = row.get("SenderMailFromDomain") or (sender.split("@")[-1] if "@" in sender else "")
        policy_type = self._detect_policy_type(row)

        # Quarantine reason: prefer policy name, fall back to type detection
        policy_name = row.get("OrgLevelPolicy") or row.get("UserLevelPolicy") or ""
        quarantine_reason = policy_name if policy_name else policy_type

        return {
            "message_id": row.get("NetworkMessageId", ""),
            "internet_message_id": row.get("InternetMessageId", ""),
            "sender_address": sender,
            "sender_display_name": row.get("SenderDisplayName", ""),
            "sender_domain": domain.lower(),
            "recipient_address": (row.get("RecipientEmailAddress") or "").lower(),
            "subject": row.get("Subject", ""),
            "received_time": row.get("Timestamp"),
            "expiry_time": None,
            "quarantine_reason": quarantine_reason,
            "policy_type": policy_type,
            "threat_type": threat_type,
            "spf_result": spf,
            "dkim_result": dkim,
            "dmarc_result": dmarc,
            "url_count": int(row.get("UrlCount") or 0),
            "attachment_count": int(row.get("AttachmentCount") or 0),
            "sender_ip": row.get("SenderIPv4") or row.get("SenderIPv6") or "",
            "email_direction": row.get("EmailDirection") or "",
            "release_status": self._detect_release_status(row),
            "latest_delivery_action": row.get("LatestDeliveryAction", ""),
            "latest_delivery_location": row.get("LatestDeliveryLocation", ""),
        }

    def _detect_release_status(self, row: dict) -> str:
        """Map Defender delivery fields to a unified status: Delivered|Junk|Quarantined|Blocked|Released|Deleted."""
        action      = (row.get("LatestDeliveryAction") or "").lower()
        loc         = (row.get("LatestDeliveryLocation") or "").lower()
        orig_action = (row.get("DeliveryAction") or "").lower()
        orig_loc    = (row.get("DeliveryLocation") or "").lower()
        # Blocked — never entered the mailbox
        if "block" in action or "block" in orig_action:
            return "Blocked"
        # Deleted — removed from quarantine
        if "delete" in action or "deleted" in loc:
            return "Deleted"
        # Released from quarantine back to inbox
        if "release" in action:
            return "Released"
        # Quarantined
        if "quarantine" in action or "quarantine" in loc or "quarantine" in orig_loc:
            return "Quarantined"
        # Junk folder delivery
        if "junk" in loc or "junk" in orig_loc:
            return "Junk"
        # Normal delivery to inbox / folders
        if "inbox" in loc or "inbox" in orig_loc or "folder" in loc or "deliver" in action:
            return "Delivered"
        return "Unknown"

    def _extract_auth(self, auth_str: str, protocol: str) -> str:
        """Extract pass/fail/neutral result for spf/dkim/dmarc from auth string.

        Handles both JSON format ({"SPF":"pass",...}) and legacy string format (spf=pass;...).
        """
        if not auth_str:
            return "none"
        # JSON format: {"SPF":"pass","DKIM":"fail",...}
        if auth_str.strip().startswith("{"):
            try:
                data = json.loads(auth_str)
                val = data.get(protocol.upper()) or data.get(protocol.lower()) or ""
                return val.lower() if val else "none"
            except (ValueError, AttributeError):
                pass
        # Legacy string format: spf=pass; dkim=fail; ...
        pattern = re.compile(rf"{protocol}[=:]\s*(pass|fail|neutral|none|softfail|permerror|temperror)", re.I)
        m = pattern.search(auth_str)
        return m.group(1).lower() if m else "none"

    def _detect_policy_type(self, row: dict) -> str:
        threat = (row.get("ThreatTypes") or "").lower()
        threat_names = (row.get("ThreatNames") or "").lower()
        detection = (row.get("DetectionMethods") or "").lower()
        policy = (row.get("OrgLevelPolicy") or row.get("UserLevelPolicy") or "").lower()
        combined = threat + " " + threat_names + " " + detection + " " + policy
        if "malware" in combined:
            return "Anti-Malware"
        if "phish" in combined:
            return "Anti-Phish"
        if "transport" in combined or "rule" in policy:
            return "Transport Rule"
        if "spam" in combined or (row.get("BulkComplaintLevel") or 0) > 4:
            return "Anti-Spam"
        return "Unknown"

    # ── Release / Delete actions via Microsoft Defender REST API ────────────────
    # Requires SecurityEvents.ReadWrite.All (Application permission, admin consented).
    # No Exchange PS RBAC role assignment needed.

    def release_message(self, message_id: str, recipient: str) -> dict:
        """
        Release a quarantined message to the recipient's inbox.
        Uses the Defender for Office 365 remediation API.
        """
        try:
            resp = requests.post(
                f"{_SECURITY_API}/api/quarantine/release",
                headers=self._security_headers(),
                json={"NetworkMessageId": message_id, "RecipientAddress": recipient},
                timeout=30,
            )
            resp.raise_for_status()
            return {"success": True, "result": resp.json() if resp.content else {}}
        except requests.HTTPError as e:
            body = ""
            try:
                body = e.response.json().get("error", {}).get("message", "") if e.response else ""
            except Exception:
                pass
            logger.error("Release failed for %s: %s — %s", message_id, e, body)
            return {"success": False, "error": body or str(e)}
        except Exception as e:
            logger.error("Release error for %s: %s", message_id, e)
            return {"success": False, "error": str(e)}

    def delete_message(self, message_id: str) -> dict:
        """
        Soft-delete a quarantined message (marks it for deletion).
        Uses the Defender for Office 365 remediation API.
        """
        try:
            resp = requests.post(
                f"{_SECURITY_API}/api/quarantine/delete",
                headers=self._security_headers(),
                json={"NetworkMessageId": message_id},
                timeout=30,
            )
            resp.raise_for_status()
            return {"success": True, "result": resp.json() if resp.content else {}}
        except requests.HTTPError as e:
            body = ""
            try:
                body = e.response.json().get("error", {}).get("message", "") if e.response else ""
            except Exception:
                pass
            logger.error("Delete failed for %s: %s — %s", message_id, e, body)
            return {"success": False, "error": body or str(e)}
        except Exception as e:
            logger.error("Delete error for %s: %s", message_id, e)
            return {"success": False, "error": str(e)}

    def delete_mailbox_message(self, recipient: str, internet_message_id: str) -> dict:
        """
        Delete a delivered, junked, or released message from the recipient mailbox.
        Uses Microsoft Graph and requires Mail.ReadWrite application permission.
        """
        if not recipient:
            return {"success": False, "error": "Recipient address is required to delete mailbox messages."}

        iid = self._normalize_internet_message_id(internet_message_id)
        if not iid:
            return {"success": False, "error": "Internet message ID is required to delete mailbox messages."}

        try:
            hdrs_g = self._graph_headers()
            lookup = requests.get(
                f"{_GRAPH_BASE}/users/{recipient}/messages",
                headers=hdrs_g,
                params={
                    "$filter": f"internetMessageId eq '{iid}'",
                    "$select": "id",
                    "$top": "1",
                },
                timeout=20,
            )
            if lookup.status_code == 403:
                return {"success": False, "error": "Mail.ReadWrite permission is required to delete mailbox messages."}
            lookup.raise_for_status()

            messages = lookup.json().get("value", [])
            if not messages:
                return {"success": False, "error": "Message was not found in the recipient mailbox."}

            graph_message_id = messages[0].get("id")
            if not graph_message_id:
                return {"success": False, "error": "Graph did not return a mailbox message ID."}

            resp = requests.delete(
                f"{_GRAPH_BASE}/users/{recipient}/messages/{graph_message_id}",
                headers=hdrs_g,
                timeout=20,
            )
            if resp.status_code == 403:
                return {"success": False, "error": "Mail.ReadWrite permission is required to delete mailbox messages."}
            resp.raise_for_status()
            return {"success": True}
        except requests.HTTPError as e:
            body = ""
            try:
                body = e.response.json().get("error", {}).get("message", "") if e.response else ""
            except Exception:
                pass
            logger.error("Mailbox delete failed for %s / %s: %s — %s", recipient, iid, e, body)
            return {"success": False, "error": body or str(e)}
        except Exception as e:
            logger.error("Mailbox delete error for %s / %s: %s", recipient, iid, e)
            return {"success": False, "error": str(e)}

    def get_message_headers(self, message_id: str) -> str | None:
        """
        Retrieve email headers via Advanced Hunting — no extra permissions needed.
        Pulls the AuthenticationDetails and other header fields from EmailEvents.
        """
        kql = f"""
EmailEvents
| where NetworkMessageId == "{message_id}"
| project
    NetworkMessageId,
    AuthenticationDetails,
    EmailDirection,
    SenderIPv4,
    SenderIPv6,
    SenderMailFromAddress,
    SenderFromAddress,
    SenderDisplayName,
    SenderMailFromDomain,
    RecipientEmailAddress,
    Subject,
    Timestamp,
    ThreatTypes,
    ThreatNames,
    DetectionMethods,
    ConfidenceLevel,
    BulkComplaintLevel,
    LatestDeliveryAction,
    LatestDeliveryLocation
| take 1
"""
        try:
            resp = requests.post(
                f"{_SECURITY_API}/api/advancedhunting/run",
                headers=self._security_headers(),
                json={"Query": kql},
                timeout=30,
            )
            resp.raise_for_status()
            results = resp.json().get("Results", [])
            if not results:
                return None
            row = results[0]
            # Format as pseudo-headers string for display
            lines = []
            for k, v in row.items():
                if v:
                    lines.append(f"{k}: {v}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning("Header fetch via hunting failed for %s: %s", message_id, e)
            return None

    # ── Email preview (body + URLs + attachments) ─────────────────────────────

    def get_email_preview(self, message_id: str, internet_message_id: str = None,
                          recipient: str = None, release_status: str = None) -> dict:
        """
        Fetch full preview data for a message.
        Strategy:
          1. Graph API /users/{recipient}/messages — full HTML body (requires Mail.Read).
             Falls back gracefully if permission not granted (returns body_available=False).
          2. Advanced Hunting EmailUrlInfo    — URLs in the message.
          3. Advanced Hunting EmailAttachmentInfo — attachment metadata + hashes.
        """
        result: dict = {
            "body_html": None,
            "body_preview": None,
            "body_available": False,
            "urls": [],
            "attachments": [],
            "error": None,
        }
        base   = f"{_SECURITY_API}/api/advancedhunting/run"
        hdrs_s = self._security_headers()

        # ── 1. Body via Graph API ─────────────────────────────────────────────
        # Only possible for messages that are (or were) in a mailbox:
        # Delivered, Junk, Released.  Quarantined/Blocked messages are not in mailboxes.
        in_mailbox = release_status in ("Delivered", "Junk", "Released") if release_status else True
        if in_mailbox and internet_message_id and recipient:
            try:
                hdrs_g = self._graph_headers()
                iid = self._normalize_internet_message_id(internet_message_id)
                url = f"{_GRAPH_BASE}/users/{recipient}/messages"
                params = {
                    "$filter": f"internetMessageId eq '{iid}'",
                    "$select": "subject,body,bodyPreview,receivedDateTime,from",
                    "$top": "1",
                }
                r = requests.get(url, headers=hdrs_g, params=params, timeout=20)
                if r.status_code == 403:
                    # Mail.Read not yet granted — set a helpful flag but don't error out
                    result["body_available"] = False
                    result["body_permission_needed"] = True
                elif r.ok:
                    msgs = r.json().get("value", [])
                    if msgs:
                        body = msgs[0].get("body", {})
                        content = body.get("content", "") if body else ""
                        preview = msgs[0].get("bodyPreview", "")
                        if content and len(content) > 10:
                            result["body_html"] = content
                            result["body_available"] = True
                        elif preview:
                            result["body_preview"] = preview
                            result["body_available"] = True
            except Exception as e:
                logger.debug("Graph body fetch failed for %s: %s", message_id, e)

        # ── 2. URLs via Advanced Hunting ──────────────────────────────────────
        try:
            kql2 = f'EmailUrlInfo | where NetworkMessageId == "{message_id}" | project Url, UrlDomain, UrlLocation'
            r2 = requests.post(base, headers=hdrs_s, json={"Query": kql2}, timeout=30)
            r2.raise_for_status()
            result["urls"] = r2.json().get("Results", [])
        except Exception as e:
            logger.debug("EmailUrlInfo failed for %s: %s", message_id, e)

        # ── 3. Attachments via Advanced Hunting ───────────────────────────────
        try:
            kql3 = (
                f'EmailAttachmentInfo | where NetworkMessageId == "{message_id}"'
                ' | project FileName, FileType, FileSize, SHA256, MalwareFamily, ThreatNames, DetectionMethods'
            )
            r3 = requests.post(base, headers=hdrs_s, json={"Query": kql3}, timeout=30)
            r3.raise_for_status()
            result["attachments"] = r3.json().get("Results", [])
        except Exception as e:
            logger.debug("EmailAttachmentInfo failed for %s: %s", message_id, e)

        return result

    # ── Phishing intelligence ─────────────────────────────────────────────────

    @staticmethod
    def extract_iocs(message: dict) -> list[dict]:
        """
        Extract IOCs from a normalized message dict.
        Returns list of {ioc_type, ioc_value, threat_label}.
        """
        iocs = []
        domain = (message.get("sender_domain") or "").lower().strip()
        if domain and domain not in ("gmail.com", "outlook.com", "yahoo.com", "hotmail.com"):
            iocs.append({
                "ioc_type": "domain",
                "ioc_value": domain,
                "threat_label": message.get("threat_type", "Unknown"),
            })

        sender = (message.get("sender_address") or "").lower().strip()
        if sender:
            iocs.append({
                "ioc_type": "email",
                "ioc_value": sender,
                "threat_label": message.get("threat_type", "Unknown"),
            })

        urls_raw = message.get("urls_json")
        if urls_raw:
            try:
                for url in json.loads(urls_raw):
                    iocs.append({
                        "ioc_type": "url",
                        "ioc_value": url,
                        "threat_label": message.get("threat_type", "Unknown"),
                    })
                    parsed = urlparse(url)
                    if parsed.netloc:
                        iocs.append({
                            "ioc_type": "domain",
                            "ioc_value": parsed.netloc.lower(),
                            "threat_label": message.get("threat_type", "Unknown"),
                        })
            except (json.JSONDecodeError, Exception):
                pass

        return iocs

    @staticmethod
    def compute_risk_score(message: dict) -> int:
        """
        Score 0-100 based on threat indicators.
        High score = high confidence phishing / malware.
        """
        score = 0
        tt = (message.get("threat_type") or "").lower()
        if tt == "phish":
            score += 40
        elif tt == "malware":
            score += 50
        elif tt == "spam":
            score += 10

        if (message.get("spf_result") or "").lower() in ("fail", "softfail"):
            score += 20
        if (message.get("dkim_result") or "").lower() == "fail":
            score += 20
        if (message.get("dmarc_result") or "").lower() == "fail":
            score += 15

        if int(message.get("url_count") or 0) > 3:
            score += 5
        if int(message.get("attachment_count") or 0) > 0:
            score += 5

        return min(score, 100)

    @staticmethod
    def cluster_campaigns(messages: list) -> dict[str, list]:
        """
        Group messages into probable campaigns by sender domain.
        Returns {campaign_id: [message_id, ...]} mapping.
        """
        from collections import defaultdict
        campaigns: dict[str, list] = defaultdict(list)
        for m in messages:
            domain = getattr(m, "sender_domain", None) or (m.get("sender_domain") if isinstance(m, dict) else None)
            if domain:
                campaigns[domain].append(
                    getattr(m, "message_id", None) or (m.get("message_id") if isinstance(m, dict) else None)
                )
        # Only return groups of 2+ messages (actual campaigns)
        return {k: v for k, v in campaigns.items() if len(v) >= 2}

    # ── Test connection ───────────────────────────────────────────────────────

    def test_connection(self) -> dict:
        """Validate credentials by fetching a token."""
        try:
            token = self._graph_token()
            return {"success": True, "message": "Graph token obtained successfully"}
        except Exception as e:
            return {"success": False, "message": str(e)}
