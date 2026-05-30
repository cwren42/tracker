"""LDAP / On-Prem AD integration service.

This is intentionally minimal and uses Settings keys (in the tracker DB)
for configuration.

Setting keys used:
- ad_enabled: 'true'/'false'
- ad_server
- ad_port
- ad_use_ssl: 'true'/'false'
- ad_base_dn
- ad_bind_username
- ad_bind_password
- ad_user_ou_dn (optional; defaults to ad_base_dn)
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from ldap3 import Server, Connection, ALL, SUBTREE, MODIFY_REPLACE, MODIFY_ADD, MODIFY_DELETE
from ldap3.core.exceptions import LDAPException

logger = logging.getLogger(__name__)


@dataclass
class ADConfig:
    enabled: bool
    server: str
    port: int
    use_ssl: bool
    base_dn: str
    bind_username: str
    bind_password: str
    user_ou_dn: str          # search base for user sync (also creation OU)
    computer_ou_dn: str = '' # OU for computer/asset sync
    ou_as_department: bool = True  # infer dept from OU path when AD dept attr is blank


def _setting_bool(value: Optional[str]) -> bool:
    return (value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def load_ad_config(Setting):
    """Load AD config from Setting model."""
    def get(key: str) -> Optional[str]:
        row = Setting.query.filter_by(key=key).first()
        return row.value if row and row.value is not None else None

    enabled = _setting_bool(get('ad_enabled'))
    server = (get('ad_server') or '').strip()
    port = int((get('ad_port') or '636').strip() or '636')
    use_ssl = _setting_bool(get('ad_use_ssl'))
    base_dn = (get('ad_base_dn') or '').strip()
    bind_username = (get('ad_bind_username') or '').strip()
    from secret_store import decrypt_secret
    bind_password = decrypt_secret(get('ad_bind_password')) or ''
    user_ou_dn = (get('ad_user_ou_dn') or base_dn).strip()
    computer_ou_dn = (get('ad_computer_ou_dn') or '').strip()
    ou_as_department = _setting_bool(get('ad_ou_as_department') or 'true')

    return ADConfig(
        enabled=enabled,
        server=server,
        port=port,
        use_ssl=use_ssl,
        base_dn=base_dn,
        bind_username=bind_username,
        bind_password=bind_password,
        user_ou_dn=user_ou_dn,
        computer_ou_dn=computer_ou_dn,
        ou_as_department=ou_as_department,
    )


class LDAPService:
    def __init__(self, config: ADConfig):
        self.config = config
        self.connection: Optional[Connection] = None

    def connect(self) -> None:
        if not self.config.enabled:
            raise Exception('AD integration is disabled')
        if not self.config.server or not self.config.base_dn or not self.config.bind_username:
            raise Exception('AD settings incomplete')

        try:
            server = Server(self.config.server, port=self.config.port, use_ssl=self.config.use_ssl, get_info=ALL)
            self.connection = Connection(
                server,
                user=self.config.bind_username,
                password=self.config.bind_password,
                auto_bind=True,
            )
        except LDAPException as e:
            raise Exception(str(e))

    def disconnect(self) -> None:
        if self.connection:
            try:
                self.connection.unbind()
            except Exception:
                pass
        self.connection = None

    def test_connection(self) -> dict:
        try:
            self.connect()
            return {'success': True, 'message': 'Connected'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            self.disconnect()

    def _ensure(self):
        if not self.connection:
            self.connect()

    def find_user(self, username_or_upn: str):
        self._ensure()
        search_filter = f"(&(objectClass=user)(|(sAMAccountName={username_or_upn})(userPrincipalName={username_or_upn})(mail={username_or_upn})))"
        self.connection.search(
            search_base=self.config.base_dn,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=['distinguishedName', 'sAMAccountName', 'userPrincipalName', 'mail', 'displayName', 'userAccountControl']
        )
        if self.connection.entries:
            return self.connection.entries[0]
        return None

    def disable_user(self, username_or_upn: str) -> dict:
        try:
            user = self.find_user(username_or_upn)
            if not user:
                return {'success': False, 'error': f'User {username_or_upn} not found in AD'}

            current_uac = int(user.userAccountControl.value)
            new_uac = current_uac | 2
            self.connection.modify(user.entry_dn, {'userAccountControl': [(MODIFY_REPLACE, [new_uac])]})

            if self.connection.result['result'] == 0:
                return {'success': True, 'message': f'User {username_or_upn} disabled', 'dn': user.entry_dn}
            return {'success': False, 'error': self.connection.result.get('description')}
        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            self.disconnect()

    def enable_user(self, username_or_upn: str) -> dict:
        try:
            user = self.find_user(username_or_upn)
            if not user:
                return {'success': False, 'error': f'User {username_or_upn} not found in AD'}

            current_uac = int(user.userAccountControl.value)
            new_uac = current_uac & ~2
            self.connection.modify(user.entry_dn, {'userAccountControl': [(MODIFY_REPLACE, [new_uac])]})

            if self.connection.result['result'] == 0:
                return {'success': True, 'message': f'User {username_or_upn} enabled', 'dn': user.entry_dn}
            return {'success': False, 'error': self.connection.result.get('description')}
        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            self.disconnect()

    def delete_user(self, username_or_upn: str) -> dict:
        try:
            user = self.find_user(username_or_upn)
            if not user:
                return {'success': False, 'error': f'User {username_or_upn} not found in AD'}

            self.connection.delete(user.entry_dn)
            if self.connection.result['result'] == 0:
                return {'success': True, 'message': f'User {username_or_upn} deleted', 'dn': user.entry_dn}
            return {'success': False, 'error': self.connection.result.get('description')}
        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            self.disconnect()

    def add_user_to_group(self, username_or_upn: str, group_dn: str) -> dict:
        try:
            user = self.find_user(username_or_upn)
            if not user:
                return {'success': False, 'error': f'User {username_or_upn} not found in AD'}

            self._ensure()
            self.connection.modify(group_dn, {'member': [(MODIFY_ADD, [user.entry_dn])]})
            if self.connection.result['result'] == 0:
                return {'success': True, 'message': f'Added {username_or_upn} to group', 'user_dn': user.entry_dn, 'group_dn': group_dn}
            return {'success': False, 'error': self.connection.result.get('description')}
        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            self.disconnect()

    def remove_user_from_group(self, username_or_upn: str, group_dn: str) -> dict:
        try:
            user = self.find_user(username_or_upn)
            if not user:
                return {'success': False, 'error': f'User {username_or_upn} not found in AD'}

            self._ensure()
            self.connection.modify(group_dn, {'member': [(MODIFY_DELETE, [user.entry_dn])]})
            if self.connection.result['result'] == 0:
                return {'success': True, 'message': f'Removed {username_or_upn} from group', 'user_dn': user.entry_dn, 'group_dn': group_dn}
            return {'success': False, 'error': self.connection.result.get('description')}
        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            self.disconnect()

    def create_user(
        self,
        username: str,
        user_principal_name: str,
        display_name: str,
        email: Optional[str] = None,
        given_name: Optional[str] = None,
        surname: Optional[str] = None,
        password: Optional[str] = None,
        enable: bool = True,
        ou_dn: Optional[str] = None,
    ) -> dict:
        """Create an AD user.

        Notes:
        - Setting passwords typically requires LDAPS.
        - This creates the user disabled first, then (optionally) sets password and enables.
        """
        try:
            self._ensure()

            username = (username or '').strip()
            user_principal_name = (user_principal_name or '').strip()
            display_name = (display_name or '').strip()
            if not username or not user_principal_name or not display_name:
                return {'success': False, 'error': 'username, user_principal_name, and display_name are required'}

            # Basic name parsing fallback
            if not given_name or not surname:
                parts = [p for p in display_name.split(' ') if p]
                if not given_name and parts:
                    given_name = parts[0]
                if not surname:
                    surname = parts[-1] if len(parts) > 1 else 'User'

            target_ou = (ou_dn or self.config.user_ou_dn or self.config.base_dn).strip()
            user_dn = f"CN={display_name},{target_ou}"

            attributes = {
                'sAMAccountName': username,
                'userPrincipalName': user_principal_name,
                'displayName': display_name,
                'givenName': given_name,
                'sn': surname,
            }
            if email:
                attributes['mail'] = email

            # Create as disabled (514) until password set
            attributes['userAccountControl'] = 514

            ok = self.connection.add(
                dn=user_dn,
                object_class=['top', 'person', 'organizationalPerson', 'user'],
                attributes=attributes,
            )

            if not ok or self.connection.result['result'] != 0:
                return {'success': False, 'error': self.connection.result.get('message') or self.connection.result.get('description'), 'dn': user_dn}

            password_set = False
            if password:
                if not self.config.use_ssl:
                    return {
                        'success': False,
                        'error': 'Password set requires LDAPS (enable ad_use_ssl and port 636)',
                        'dn': user_dn,
                    }
                try:
                    # ldap3 microsoft extension
                    self.connection.extend.microsoft.modify_password(user_dn, password)
                    if self.connection.result['result'] == 0:
                        password_set = True
                    else:
                        return {'success': False, 'error': self.connection.result.get('description'), 'dn': user_dn}
                except Exception as e:
                    return {'success': False, 'error': str(e), 'dn': user_dn}

            if enable and password_set:
                # Enable account (clear disable bit)
                self.connection.modify(user_dn, {'userAccountControl': [(MODIFY_REPLACE, [512])]})
                if self.connection.result['result'] != 0:
                    return {'success': False, 'error': self.connection.result.get('description'), 'dn': user_dn}

            return {
                'success': True,
                'message': 'User created',
                'dn': user_dn,
                'password_set': password_set,
                'enabled': bool(enable and password_set),
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            self.disconnect()

    # ------------------------------------------------------------------
    # Bulk user enumeration — used for employee sync (AD is master)
    # ------------------------------------------------------------------

    def get_all_users(self) -> list:
        """Return all user objects from AD as a list of plain dicts.

        Pulls every objectClass=user / objectCategory=person entry (both
        enabled and disabled) so the caller can decide what to do with
        disabled accounts.

        Uses ldap3's paged_search so it works on directories with > 1000
        entries (AD's default page size limit).

        When config.ou_as_department is True (default), the OU hierarchy in
        the user's DN is used to fill in department and/or location when the
        AD department attribute is blank.  The logic mirrors this structure:

            OU=Engineering,OU=CirqueUS,OU=CirqueUsers,...
                → department="Engineering", location="US"

            OU=China,OU=CirqueTaiwan,OU=CirqueUsers,...
                → department=(from AD attr or "China"), location="Taiwan"

            OU=CirqueTaiwan,OU=CirqueUsers,...
                → location="Taiwan"
        """
        self._ensure()

        # Use the user_ou_dn as the search base when configured (narrows to
        # just the CirqueUsers subtree instead of the whole domain)
        search_base = self.config.user_ou_dn or self.config.base_dn

        AD_ATTRS = [
            'objectGUID',
            'sAMAccountName',
            'distinguishedName',
            'displayName',
            'mail',
            'userPrincipalName',
            'givenName',
            'sn',
            'department',
            'title',
            'telephoneNumber',
            'mobile',
            'userAccountControl',
            'thumbnailPhoto',
        ]

        # Exclude disabled accounts at the query level (UAC bit 2 = ACCOUNTDISABLE)
        search_filter = "(&(objectClass=user)(objectCategory=person)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))"

        raw_entries = self.connection.extend.standard.paged_search(
            search_base=search_base,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=AD_ATTRS,
            paged_size=250,
            generator=False,
        )

        results = []
        for entry in (raw_entries or []):
            # paged_search returns dicts with 'dn' and 'attributes' keys
            if not isinstance(entry, dict):
                continue
            dn = entry.get('dn', '')
            if not dn:
                continue
            attrs = entry.get('attributes', {})

            # --- objectGUID ---
            raw_guid = attrs.get('objectGUID')
            if not raw_guid:
                continue
            try:
                if isinstance(raw_guid, bytes):
                    guid_str = str(uuid.UUID(bytes_le=raw_guid))
                elif isinstance(raw_guid, list) and raw_guid:
                    g = raw_guid[0]
                    guid_str = str(uuid.UUID(bytes_le=g)) if isinstance(g, bytes) else str(g).strip('{}')
                else:
                    guid_str = str(raw_guid).strip('{}')
            except Exception:
                continue

            # --- userAccountControl → enabled? ---
            uac = attrs.get('userAccountControl', 0)
            if isinstance(uac, list):
                uac = uac[0] if uac else 0
            try:
                enabled = not bool(int(uac) & 2)
            except (ValueError, TypeError):
                enabled = True

            def _str(v):
                if v is None:
                    return ''
                if isinstance(v, list):
                    v = v[0] if v else ''
                if isinstance(v, bytes):
                    return ''
                return str(v).strip()

            # thumbnail (bytes)
            thumb = attrs.get('thumbnailPhoto')
            if isinstance(thumb, list):
                thumb = thumb[0] if thumb else None
            thumb = thumb if isinstance(thumb, bytes) else None

            mail = _str(attrs.get('mail'))
            upn = _str(attrs.get('userPrincipalName'))
            email = mail or upn   # mail is preferred; fall back to UPN

            phone = _str(attrs.get('telephoneNumber')) or _str(attrs.get('mobile'))
            ad_dept  = _str(attrs.get('department')) or None
            ad_title = _str(attrs.get('title')) or None

            # --- OU-based department / location inference ---
            ou_dept, ou_location = self._parse_ou_dept_location(dn)
            department = ad_dept or (ou_dept if self.config.ou_as_department else None)
            location   = ou_location or None

            results.append({
                'ad_guid':           guid_str,
                'sam_account_name':  _str(attrs.get('sAMAccountName')),
                'distinguished_name': dn,
                'display_name':      _str(attrs.get('displayName')),
                'email':             email,
                'upn':               upn,
                'given_name':        _str(attrs.get('givenName')),
                'surname':           _str(attrs.get('sn')),
                'department':        department,
                'title':             ad_title,
                'phone':             phone or None,
                'ad_enabled':        enabled,
                'thumbnail_photo':   thumb,
                'ou_location':       location,   # inferred region from OU path
            })

        return results

    @staticmethod
    def _parse_ou_dept_location(dn: str):
        """Extract (department, location) from a Distinguished Name.

        Parses the OU components out of the DN left-to-right (most-specific
        first) and applies a simple set of conventions:

        Known "region" OU prefixes (case-insensitive):
            CirqueUS     → location = 'US'
            CirqueTaiwan → location = 'Taiwan'
            CirqueChina / Cirque-China → location = 'China'

        The first OU inside a region OU is treated as the department.
        OUs named after the whole company tree (CirqueUsers, CirqueComputers,
        Builtin, etc.) are ignored.

        Returns (department: str|None, location: str|None).
        """
        # Pull just the OU= components, most-specific first
        ous = []
        for part in dn.split(','):
            part = part.strip()
            if part.upper().startswith('OU='):
                ous.append(part[3:])   # strip 'OU='

        # Normalise / skip structural OUs
        SKIP = {'cirqueusers', 'cirquecomputers', 'cirquecomputersazure',
                'cirquegroups', 'cirquegroupsazure', 'cirqueservers',
                'cirquecompany', 'builtin', 'users', 'computers',
                'cirque-domain-users', 'cirqueadmins', 'domain controllers'}

        REGION_MAP = {
            'cirqueus': 'US',
            'cirquetaiwan': 'Taiwan',
            'cirquechina': 'China',
            'cirque-china': 'China',
            'china': 'China',
        }

        department = None
        location   = None

        # Walk from innermost (most specific) to outermost
        for i, ou in enumerate(ous):
            ou_lower = ou.lower()
            if ou_lower in SKIP:
                continue
            region = REGION_MAP.get(ou_lower)
            if region:
                location = region
                # The OU immediately inside the region is the dept (if we
                # haven't found one yet and it's more specific than the region)
                if department is None and i > 0:
                    inner = ous[i - 1].lower()
                    if inner not in SKIP and inner not in REGION_MAP:
                        department = ous[i - 1]
            elif department is None:
                department = ou   # first non-structural, non-region OU

        return department, location
