import os
import re
import json
import sqlite3
import shutil
import tempfile
import datetime
import subprocess


def _snappy_decompress(buf):
    pos = 0
    n = len(buf)
    ulen = 0
    shift = 0
    while pos < n:
        b = buf[pos]
        pos += 1
        ulen |= (b & 0x7f) << shift
        if not (b & 0x80):
            break
        shift += 7
    out = bytearray()
    while pos < n and len(out) < ulen:
        tag = buf[pos]
        pos += 1
        ttype = tag & 0x03
        if ttype == 0:
            length = (tag >> 2) + 1
            if length > 60:
                eb = length - 60
                length = int.from_bytes(buf[pos:pos + eb], 'little') + 1
                pos += eb
            out.extend(buf[pos:pos + length])
            pos += length
        else:
            if ttype == 1:
                length = ((tag >> 2) & 0x07) + 4
                offset = ((tag & 0xe0) << 3) | buf[pos]
                pos += 1
            elif ttype == 2:
                length = (tag >> 2) + 1
                offset = int.from_bytes(buf[pos:pos + 2], 'little')
                pos += 2
            else:
                length = (tag >> 2) + 1
                offset = int.from_bytes(buf[pos:pos + 4], 'little')
                pos += 4
            if offset <= 0 or offset > len(out):
                break
            if offset >= length:
                start = len(out) - offset
                out.extend(out[start:start + length])
            else:
                start = len(out) - offset
                chunk = out[start:] * ((length // offset) + 1)
                out.extend(chunk[:length])
    return bytes(out)


# Strictly matches only real chat message / conversation timestamps inside Claude's IndexedDB
# (never matches user account login/profile timestamps like age_is_verified or grove_updated_at)
_CHAT_TS_PAT = re.compile(
    rb'(?:'
    rb'(?:start_timestamp|stop_timestamp|latest_assistant_output_at|last_settled_at)"[\x10-\x20](202[5-9]-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z)'
    rb'|'
    rb'sender"(?:\x05human|\tassistant).{1,30}?updated_at"[\x10-\x20](202[5-9]-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z)'
    rb'|'
    rb'updated_at"[\x10-\x20](202[5-9]-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z)"(?:\ttruncated|\x0bstop_reason)'
    rb'|'
    rb'uuid"\$[0-9a-f\-]{36}"\x04name".{1,240}?updated_at"[\x10-\x20](202[5-9]-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z)'
    rb')',
    re.DOTALL
)


class ChromeManager:
    def __init__(self):
        self.user_data_dir = os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data')
        self.app_data_dir = os.path.join(os.path.expandvars(r'%LOCALAPPDATA%'), 'ClaudeTakipci')
        self.app_sessions_dir = os.path.join(self.app_data_dir, 'sessions')
        self.cache_file = os.path.join(self.app_data_dir, 'idb_cache.json')
        self.local_state_path = os.path.join(self.user_data_dir, 'Local State')
        self.chrome_exe = self._find_chrome_exe()
        self._idb_cache = self._load_cache()

    def _load_cache(self):
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            return {k: (v[0], v[1], v[2].encode('ascii') if v[2] else None) for k, v in raw.items()}
        except Exception:
            return {}

    def _save_cache(self):
        try:
            os.makedirs(self.app_data_dir, exist_ok=True)
            serializable = {k: [v[0], v[1], v[2].decode('ascii') if v[2] else None] for k, v in self._idb_cache.items()}
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(serializable, f)
        except Exception:
            pass

    def _find_chrome_exe(self):
        candidates = [
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe')
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return 'chrome.exe'

    @staticmethod
    def chrome_time_to_datetime(chrome_time_val):
        if not chrome_time_val:
            return None
        try:
            epoch = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
            dt = epoch + datetime.timedelta(microseconds=chrome_time_val)
            return dt.astimezone()
        except Exception:
            return None

    @staticmethod
    def iso_to_datetime(iso_bytes):
        if not iso_bytes:
            return None
        try:
            s = iso_bytes.decode('ascii') if isinstance(iso_bytes, (bytes, bytearray)) else str(iso_bytes)
            return datetime.datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone()
        except Exception:
            return None

    @staticmethod
    def format_relative_time(dt: datetime.datetime) -> str:
        if not dt:
            return "Kayıt yok"
        now = datetime.datetime.now(dt.tzinfo)
        diff = now - dt
        total_seconds = int(diff.total_seconds())

        if total_seconds < 0:
            return dt.strftime('%d.%m.%Y %H:%M')

        if total_seconds < 60:
            return "Az önce"
        if total_seconds < 3600:
            minutes = total_seconds // 60
            return f"{minutes} dk önce ({dt.strftime('%H:%M')})"

        is_today = (dt.date() == now.date())
        is_yesterday = (dt.date() == (now - datetime.timedelta(days=1)).date())

        if is_today:
            hours = total_seconds // 3600
            return f"Bugün {dt.strftime('%H:%M')} ({hours} saat önce)"
        if is_yesterday:
            return f"Dün {dt.strftime('%H:%M')}"

        if diff.days < 7:
            return f"{diff.days} gün önce ({dt.strftime('%d.%m %H:%M')})"
        return dt.strftime('%d.%m.%Y %H:%M')

    def _scan_idb_parent(self, idb_parent):
        """Scans Claude's IndexedDB under idb_parent for actual message/conversation timestamps."""
        best = None
        for sub in ('https_claude.ai_0.indexeddb.leveldb', 'https_claude.ai_0.indexeddb.blob'):
            folder = os.path.join(idb_parent, sub)
            if not os.path.exists(folder):
                continue
            for r, _ds, fs in os.walk(folder):
                for fn in fs:
                    if fn == 'LOCK' or fn.startswith('MANIFEST') or fn.startswith('LOG') or fn == 'CURRENT':
                        continue
                    fp = os.path.join(r, fn)
                    try:
                        st = os.stat(fp)
                    except Exception:
                        continue
                    cached = self._idb_cache.get(fp)
                    if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
                        file_best = cached[2]
                    else:
                        file_best = None
                        try:
                            with open(fp, 'rb') as f:
                                raw = f.read()
                            data = _snappy_decompress(raw[3:]) if (len(raw) > 4 and raw[0] == 0xff and raw[2] == 0x02) else raw
                            for m in _CHAT_TS_PAT.finditer(data):
                                ts = m.group(1) or m.group(2) or m.group(3) or m.group(4)
                                if file_best is None or ts > file_best:
                                    file_best = ts
                        except Exception:
                            pass
                        self._idb_cache[fp] = (st.st_mtime, st.st_size, file_best)
                    if file_best is not None and (best is None or file_best > best):
                        best = file_best
        return best

    def get_last_chat_time(self, prof_id):
        """Returns a datetime of the last actual Claude chat message for prof_id (or None)."""
        safe = ''.join(ch if ch.isalnum() else '_' for ch in prof_id)
        c_best = self._scan_idb_parent(os.path.join(self.user_data_dir, prof_id, 'IndexedDB'))
        a_best = self._scan_idb_parent(os.path.join(self.app_sessions_dir, safe, 'storage', 'IndexedDB'))
        best_iso = max([x for x in (c_best, a_best) if x is not None], default=None)
        if best_iso:
            return self.iso_to_datetime(best_iso)

        # Fallback ONLY if IndexedDB is empty (e.g. logged out) AND Chrome history has a real /chat/<uuid> URL
        info = self._get_last_claude_visit(prof_id)
        return info.get('dt')

    def get_profiles(self):
        """Returns list of profile metadata including their last actual Claude chat time."""
        if not os.path.exists(self.local_state_path):
            return []

        try:
            with open(self.local_state_path, 'r', encoding='utf-8') as f:
                state_data = json.load(f)
        except Exception as e:
            print(f"Error reading Local State: {e}")
            return []

        info_cache = state_data.get('profile', {}).get('info_cache', {})
        profiles = []

        for prof_id, info in info_cache.items():
            email = info.get('user_name', '')
            name = info.get('name') or info.get('gaia_name') or prof_id
            gaia_name = info.get('gaia_name', '')

            pic_filename = info.get('gaia_picture_file_name', 'Google Profile Picture.png')
            pic_path = os.path.join(self.user_data_dir, prof_id, pic_filename)
            if not os.path.exists(pic_path):
                pic_path_alt = os.path.join(self.user_data_dir, prof_id, 'Google Profile Picture.png')
                pic_path = pic_path_alt if os.path.exists(pic_path_alt) else None

            dt = self.get_last_chat_time(prof_id)
            claude_info = self._get_last_claude_visit(prof_id)

            profiles.append({
                'id': prof_id,
                'name': name,
                'gaia_name': gaia_name,
                'email': email if email else 'E-posta tanımlanmamış',
                'picture_path': pic_path,
                'last_claude_dt': dt,
                'last_claude_str': dt.strftime('%d.%m.%Y %H:%M:%S') if dt else 'Yazışma yok',
                'last_claude_relative': self.format_relative_time(dt) if dt else 'Yazışma yok',
                'last_claude_url': claude_info.get('url', ''),
                'last_claude_title': claude_info.get('title', ''),
                'claude_visit_count': claude_info.get('visit_count', 0)
            })

        def sort_key(p):
            claude_ts = p['last_claude_dt'].timestamp() if p['last_claude_dt'] else 0
            has_email = 1 if '@' in p['email'] else 0
            return (claude_ts, has_email, p['name'])

        profiles.sort(key=sort_key, reverse=True)
        self._save_cache()
        return profiles

    def _get_last_claude_visit(self, prof_id):
        """Fallback: checks Chrome history strictly for actual conversation URLs (https://claude.ai/chat/...)."""
        hist_path = os.path.join(self.user_data_dir, prof_id, 'History')
        if not os.path.exists(hist_path):
            return {}

        temp_db = os.path.join(tempfile.gettempdir(), f"claude_hist_{prof_id.replace(' ', '_')}_{os.getpid()}.db")
        result = {}
        try:
            shutil.copy2(hist_path, temp_db)
            conn = sqlite3.connect(temp_db)
            c = conn.cursor()
            c.execute("""
                SELECT url, title, visit_count, last_visit_time 
                FROM urls 
                WHERE url LIKE 'https://claude.ai/chat/%' 
                ORDER BY last_visit_time DESC 
                LIMIT 1
            """)
            row = c.fetchone()
            conn.close()

            if row and row[3]:
                url, title, visit_count, lvt = row
                dt = self.chrome_time_to_datetime(lvt)
                result = {
                    'url': url,
                    'title': title if title else 'Claude.ai',
                    'visit_count': visit_count,
                    'dt': dt,
                    'formatted_str': dt.strftime('%d.%m.%Y %H:%M:%S') if dt else '',
                    'relative_str': self.format_relative_time(dt) if dt else ''
                }
        except Exception:
            pass
        finally:
            if os.path.exists(temp_db):
                try:
                    os.remove(temp_db)
                except Exception:
                    pass

        return result

    def get_chat_titles(self, prof_id, limit=40):
        """Returns distinct claude.ai chat conversations (title + url) from Chrome history."""
        hist_path = os.path.join(self.user_data_dir, prof_id, 'History')
        if not os.path.exists(hist_path):
            return []
        temp_db = os.path.join(tempfile.gettempdir(), f"claude_chats_{prof_id.replace(' ', '_')}_{os.getpid()}.db")
        chats = []
        try:
            shutil.copy2(hist_path, temp_db)
            conn = sqlite3.connect(temp_db)
            c = conn.cursor()
            c.execute("""
                SELECT url, title, last_visit_time FROM urls
                WHERE url LIKE 'https://claude.ai/chat/%'
                ORDER BY last_visit_time DESC LIMIT 200
            """)
            seen = set()
            for url, title, _lvt in c.fetchall():
                key = url.split('?')[0].split('#')[0]
                if key in seen:
                    continue
                seen.add(key)
                t = (title or '').strip()
                if t.endswith(' - Claude'):
                    t = t[:-len(' - Claude')]
                if not t or t == 'Claude':
                    t = 'Adsız sohbet'
                chats.append({'url': key, 'title': t})
                if len(chats) >= limit:
                    break
            conn.close()
        except Exception:
            pass
        finally:
            if os.path.exists(temp_db):
                try:
                    os.remove(temp_db)
                except Exception:
                    pass
        return chats

    def launch_profile(self, prof_id, url='https://claude.ai'):
        """Launch Chrome with the given profile and optionally open a URL."""
        if not os.path.exists(self.chrome_exe):
            return False, f"Chrome bulunamadı: {self.chrome_exe}"
        try:
            cmd = [self.chrome_exe, f'--profile-directory={prof_id}']
            if url:
                cmd.append(url)
            subprocess.Popen(cmd)
            return True, "Chrome başlatıldı."
        except Exception as e:
            return False, str(e)
