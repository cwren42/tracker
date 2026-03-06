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
    user_ou_dn: str


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
    bind_password = get('ad_bind_password') or ''
    user_ou_dn = (get('ad_user_ou_dn') or base_dn).strip()

    return ADConfig(
        enabled=enabled,
        server=server,
        port=port,
        use_ssl=use_ssl,
        base_dn=base_dn,
        bind_username=bind_username,
        bind_password=bind_password,
        user_ou_dn=user_ou_dn,
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
