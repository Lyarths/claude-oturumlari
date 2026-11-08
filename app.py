import os
import re
import gc
import sys
import json
import time
import ctypes
import datetime

from PyQt6.QtWebEngineWidgets import QWebEngineView  # must be imported before QApplication
from PyQt6.QtWebEngineCore import (
    QWebEngineProfile, QWebEnginePage, QWebEngineSettings, QWebEngineUrlRequestInterceptor
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QListWidget, QListWidgetItem, QFrame,
    QStackedWidget, QProgressBar, QDialog, QSystemTrayIcon, QMenu
)
from PyQt6.QtGui import QPixmap, QPainter, QPainterPath, QColor, QFont, QBrush, QIcon, QAction
from PyQt6.QtCore import Qt, QSize, QUrl, QTimer, pyqtSignal

from chrome_manager import ChromeManager

APP_DATA = os.path.join(os.path.expandvars(r'%LOCALAPPDATA%'), 'ClaudeTakipci')
STATE_FILE = os.path.join(APP_DATA, 'state.json')
CLAUDE_URL = 'https://claude.ai/new'

# ---------- Palette (dark, warm) ----------
BG = '#262624'
SIDEBAR = '#1F1E1D'
BORDER = '#363431'
TEXT = '#ECEAE4'
MUTED = '#9B978D'
ACCENT = '#D97757'
HOVER = '#2C2B28'
SELECTED = '#3A3834'
RED = '#EF5A50'

DAYS_TR = ['Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar']
MONTHS_TR = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara']


# ---------- Helpers ----------
def fmt_when(ts):
    """Short Turkish timestamp of last message: 'Şimdi', 'Bugün 20:53', 'Dün 14:02', 'Cuma 09:10', '21 Eki 22:41'."""
    if not ts:
        return 'Hiç yazışılmadı'
    dt = datetime.datetime.fromtimestamp(ts)
    now = datetime.datetime.now()
    hm = dt.strftime('%H:%M')
    if 0 <= (now - dt).total_seconds() < 60:
        return 'Şimdi'
    days = (now.date() - dt.date()).days
    if days == 0:
        return f'Bugün {hm}'
    if days == 1:
        return f'Dün {hm}'
    if 1 < days < 7:
        return f'{DAYS_TR[dt.weekday()]} {hm}'
    return f'{dt.day} {MONTHS_TR[dt.month - 1]} {hm}'


def fmt_reset(ts):
    if not ts:
        return 'Limit doldu'
    dt = datetime.datetime.fromtimestamp(ts)
    days = (dt.date() - datetime.date.today()).days
    hm = dt.strftime('%H:%M')
    if days <= 0:
        return f'Limit doldu · açılış {hm}'
    if days == 1:
        return f'Limit doldu · açılış yarın {hm}'
    return f'Limit doldu · açılış {DAYS_TR[dt.weekday()]} {hm}'


_WEEKDAYS = {
    'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3, 'friday': 4, 'saturday': 5, 'sunday': 6,
    'pazartesi': 0, 'salı': 1, 'çarşamba': 2, 'perşembe': 3, 'cuma': 4, 'cumartesi': 5, 'pazar': 6,
}


def parse_reset_time(text):
    """Extract the reset moment from a Claude limit message. Returns a unix timestamp or None."""
    low = text.lower()
    m = re.search(r'(\d{1,2})[:.](\d{2})\s*([ap])\.?\s*m\.?', low) or \
        re.search(r'(\d{1,2})()\s*([ap])\.?\s*m\b', low) or \
        re.search(r'(\d{1,2})[:.](\d{2})()', low)
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    ap = m.group(3)
    if ap == 'p' and h < 12:
        h += 12
    if ap == 'a' and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    now = datetime.datetime.now()
    target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if 'tomorrow' in low or 'yarın' in low:
        target += datetime.timedelta(days=1)
    else:
        wd = next((v for k, v in sorted(_WEEKDAYS.items(), key=lambda kv: -len(kv[0])) if k in low), None)
        if wd is not None:
            delta = (wd - now.weekday()) % 7
            target += datetime.timedelta(days=delta)
        if target <= now:
            target += datetime.timedelta(days=1)
    return target.timestamp()


# Finds Claude's usage-limit notice on the page (ignores chat message bodies).
LIMIT_JS = r"""
(() => {
  const skip = '.font-claude-response, .font-claude-message, .font-user-message, [data-testid="user-message"], .prose, pre, code, [contenteditable="true"]';
  const hit = /(out of (free )?messages|usage limit|message limit|limit reached|hit (your|the) (usage )?limit|limitine ulaş|limit(iniz)? doldu|mesaj hakkın|ücretsiz mesaj)/i;
  const when = /(until|reset|resets|kadar|sıfırlan|yenilen|available again)/i;
  const els = document.querySelectorAll('[role="alert"], [role="status"], div, p, span');
  for (const el of els) {
    const t = (el.innerText || '').trim();
    if (!t || t.length > 260 || el.childElementCount > 8) continue;
    if (!hit.test(t) || !when.test(t)) continue;
    if (el.closest(skip)) continue;
    return t.replace(/\s+/g, ' ');
  }
  return '';
})()
"""

# Queries Claude's internal conversation list API inside the logged-in session to get exact last message timestamps.
SYNC_CHATS_START_JS = r"""
(() => {
  if (window.__ct_sync_busy) return;
  window.__ct_sync_busy = true;
  window.__ct_sync_res = null;
  (async () => {
    try {
      const r = await fetch('/api/organizations', {credentials: 'include'});
      if (!r.ok) { window.__ct_sync_busy = false; return; }
      const orgs = await r.json();
      const chatOrg = (Array.isArray(orgs) ? orgs : []).find(o => (o.capabilities || []).includes('chat')) || orgs[0];
      if (!chatOrg || !chatOrg.uuid) { window.__ct_sync_busy = false; return; }
      const r2 = await fetch(`/api/organizations/${chatOrg.uuid}/chat_conversations?limit=35`, {credentials: 'include'});
      if (!r2.ok) { window.__ct_sync_busy = false; return; }
      const chats = await r2.json();
      if (Array.isArray(chats)) {
        window.__ct_sync_res = JSON.stringify(chats.slice(0, 35).map(c => ({
          uuid: c.uuid,
          name: c.name || 'Adsız sohbet',
          updated_at: c.updated_at || c.created_at || ''
        })));
      }
    } catch (e) {}
    window.__ct_sync_busy = false;
  })();
})()
"""


class State:
    def __init__(self):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
        except Exception:
            self.data = {}

    def get(self, pid):
        st = self.data.setdefault(pid, {'last_messaged': 0, 'limit_until': None, 'limit': False, 'chats': {}})
        st.setdefault('last_messaged', 0)
        st.setdefault('limit_until', None)
        st.setdefault('limit', False)
        st.setdefault('chats', {})
        return st

    def save(self):
        os.makedirs(APP_DATA, exist_ok=True)
        tmp = STATE_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_FILE)


def circular_avatar(path, size, letter='U', ring=None):
    s = size * 2
    pm = QPixmap(s, s)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addEllipse(0, 0, s, s)
    p.setClipPath(clip)
    src = QPixmap(path) if path and os.path.exists(path) else QPixmap()
    if not src.isNull():
        sc = src.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(0, 0, sc, (sc.width() - s) // 2, (sc.height() - s) // 2, s, s)
    else:
        p.fillPath(clip, QBrush(QColor(ACCENT)))
        p.setPen(QColor('#FFFFFF'))
        p.setFont(QFont('Georgia', int(s * 0.4)))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, letter.upper())
    if ring:
        p.setClipping(False)
        pen = p.pen()
        pen.setColor(QColor(ring))
        pen.setWidth(4)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(2, 2, s - 4, s - 4)
    p.end()
    pm.setDevicePixelRatio(2)
    return pm


# ---------- Web pieces ----------
class PopupDialog(QDialog):
    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Giriş')
        self.resize(520, 680)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        self.view.setPage(ClaudePage(profile, self.view))
        self.view.page().windowCloseRequested.connect(self.close)
        lay.addWidget(self.view)


class SendWatcher(QWebEngineUrlRequestInterceptor):
    """Fires `sent` ONLY when the page sends a real chat completion POST request to Claude."""
    sent = pyqtSignal()

    def interceptRequest(self, info):
        try:
            if bytes(info.requestMethod()) != b'POST':
                return
            u = info.requestUrl()
            if u.host().endswith('claude.ai') and re.search(
                    r'/(completion|retry_completion|append_message)(/|$|\?)', u.path()):
                self.sent.emit()
        except Exception:
            pass


class ClaudePage(QWebEnginePage):
    def createWindow(self, _type):
        dlg = PopupDialog(self.profile(), QApplication.activeWindow())
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()
        return dlg.view.page()


class ChatPane(QWidget):
    """One persistent, isolated Claude session per Chrome profile."""
    def __init__(self, prof_id, parent=None):
        super().__init__(parent)
        self.prof_id = prof_id
        self.loaded = False
        safe = ''.join(ch if ch.isalnum() else '_' for ch in prof_id)
        self.profile = QWebEngineProfile(f'claude_{safe}', self)
        base = os.path.join(APP_DATA, 'sessions', safe)
        self.profile.setPersistentStoragePath(os.path.join(base, 'storage'))
        self.profile.setCachePath(os.path.join(base, 'cache'))
        self.watcher = SendWatcher(self)
        self.profile.setUrlRequestInterceptor(self.watcher)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.progress = QProgressBar()
        self.progress.setFixedHeight(2)
        self.progress.setTextVisible(False)
        self.progress.setObjectName('loadBar')
        lay.addWidget(self.progress)

        self.view = QWebEngineView(self)
        page = ClaudePage(self.profile, self.view)
        self.view.setPage(page)
        st = page.settings()
        st.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        st.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, True)
        st.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        page.setBackgroundColor(QColor(BG))
        self.view.loadProgress.connect(self._on_progress)
        self.view.loadFinished.connect(self._on_finished)
        lay.addWidget(self.view)
        self.view.load(QUrl(CLAUDE_URL))

    def _on_progress(self, v):
        self.progress.setVisible(v < 100)
        self.progress.setValue(v)

    def _on_finished(self, ok):
        self.progress.setVisible(False)
        self.loaded = ok

    def open(self, url):
        self.view.load(QUrl(url))

    def on_chat_page(self):
        path = self.view.url().path()
        return path.startswith('/new') or path.startswith('/chat/')


# ---------- Sidebar row ----------
class ProfileRow(QWidget):
    def __init__(self, prof):
        super().__init__()
        self.prof = prof
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(11)
        self.av = QLabel()
        self.av.setFixedSize(34, 34)
        lay.addWidget(self.av, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.name = QLabel(prof['name'])
        self.name.setObjectName('rowName')
        top.addWidget(self.name)
        self.badge = QLabel('SON')
        self.badge.setObjectName('lastBadge')
        self.badge.setFixedHeight(15)
        self.badge.setToolTip('En son bu hesapta Claude ile yazıştın')
        top.addWidget(self.badge)
        top.addStretch()
        self.when = QLabel('')
        self.when.setObjectName('rowWhen')
        top.addWidget(self.when)
        col.addLayout(top)

        self.mail = QLabel(prof['email'])
        self.mail.setObjectName('rowMail')
        col.addWidget(self.mail)
        self.limit = QLabel('')
        self.limit.setObjectName('rowLimit')
        col.addWidget(self.limit)
        col.addStretch()
        lay.addLayout(col, 1)

    def refresh(self, last_messaged, limited, limit_until, is_last):
        letter = self.prof['name'][:1] or 'U'
        self.av.setPixmap(circular_avatar(self.prof['picture_path'], 34, letter, RED if limited else None))
        self.name.setStyleSheet(f'color: {RED};' if limited else '')
        self.when.setText(fmt_when(last_messaged))
        self.badge.setVisible(is_last)
        self.limit.setVisible(limited)
        self.limit.setText(fmt_reset(limit_until) if limited else '')


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Claude Oturumları')
        self.resize(1360, 880)
        self.setMinimumSize(1000, 640)
        self.manager = ChromeManager()
        self.state = State()
        self.panes = {}
        self.rows = {}
        self.items = {}
        self.profiles_by_id = {}
        self.chrome_last_chat = {}
        self.pending_send = {}
        self.misses = {}
        self.access_order = []  # most recently viewed prof_ids first (for RAM lifecycle management)
        self.current = None
        self._force_quit = False
        self._sleeping = False
        self._build()
        self.setStyleSheet(STYLE)
        self._setup_tray()
        self.load_profiles()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(20000)

    # ----- layout -----
    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = QFrame()
        side.setObjectName('sidebar')
        side.setFixedWidth(320)
        s = QVBoxLayout(side)
        s.setContentsMargins(12, 18, 12, 12)
        s.setSpacing(8)

        head = QHBoxLayout()
        brand = QLabel('Claude Oturumları')
        brand.setObjectName('brand')
        head.addWidget(brand)
        head.addStretch()
        rb = QPushButton('↻')
        rb.setObjectName('icon')
        rb.setToolTip('Hesapları ve son yazışma saatlerini yenile')
        rb.setCursor(Qt.CursorShape.PointingHandCursor)
        rb.clicked.connect(self.load_profiles)
        head.addWidget(rb)
        s.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText('Hesap ara…')
        self.search.textChanged.connect(self.filter_profiles)
        s.addWidget(self.search)

        s.addWidget(self._section('HESAPLAR'))
        self.profile_list = QListWidget()
        self.profile_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.profile_list.currentItemChanged.connect(self.on_profile)
        s.addWidget(self.profile_list, 3)

        chats_head = QHBoxLayout()
        chats_head.addWidget(self._section('SOHBETLER'))
        chats_head.addStretch()
        nb = QPushButton('+ Yeni sohbet')
        nb.setObjectName('ghost')
        nb.setCursor(Qt.CursorShape.PointingHandCursor)
        nb.clicked.connect(lambda: self._open_in_current(CLAUDE_URL))
        chats_head.addWidget(nb)
        s.addLayout(chats_head)

        self.chat_list = QListWidget()
        self.chat_list.setObjectName('chatList')
        self.chat_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.chat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_list.itemClicked.connect(
            lambda it: self._open_in_current(it.data(Qt.ItemDataRole.UserRole)))
        s.addWidget(self.chat_list, 2)
        h.addWidget(side)

        main = QWidget()
        m = QVBoxLayout(main)
        m.setContentsMargins(0, 0, 0, 0)
        m.setSpacing(0)

        top = QFrame()
        top.setObjectName('topbar')
        top.setFixedHeight(56)
        t = QHBoxLayout(top)
        t.setContentsMargins(20, 0, 14, 0)
        t.setSpacing(10)
        self.top_avatar = QLabel()
        self.top_avatar.setFixedSize(28, 28)
        t.addWidget(self.top_avatar)
        self.top_name = QLabel('')
        self.top_name.setObjectName('topName')
        t.addWidget(self.top_name)
        self.top_mail = QLabel('')
        self.top_mail.setObjectName('topMail')
        t.addWidget(self.top_mail)
        t.addStretch()
        for text, tip, fn in (('←', 'Geri', lambda: self._view_call('back')),
                              ('↻', 'Sayfayı yenile', lambda: self._view_call('reload')),
                              ('Chrome’da aç', 'Bu profili Chrome’da aç', self.open_in_chrome)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setObjectName('ghost' if len(text) > 1 else 'icon')
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            t.addWidget(b)
        m.addWidget(top)

        self.banner = QLabel('')
        self.banner.setObjectName('banner')
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setVisible(False)
        m.addWidget(self.banner)

        self.stack = QStackedWidget()
        empty = QLabel('Soldan bir hesap seçin')
        empty.setObjectName('empty')
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(empty)
        m.addWidget(self.stack, 1)
        h.addWidget(main, 1)

    def _section(self, text):
        l = QLabel(text)
        l.setObjectName('section')
        return l

    # ----- data -----
    def last_messaged(self, pid):
        """True timestamp of the last Claude message (from app sends, API sync, or Chrome/App IndexedDB)."""
        return max(self.state.get(pid).get('last_messaged') or 0, self.chrome_last_chat.get(pid, 0))

    def load_profiles(self):
        keep = self.current['id'] if self.current else None
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        self.rows.clear()
        self.items.clear()
        profiles = self.manager.get_profiles()
        for p in profiles:
            self.profiles_by_id[p['id']] = p
            dt = p.get('last_claude_dt')
            self.chrome_last_chat[p['id']] = dt.timestamp() if dt else 0
        profiles.sort(
            key=lambda p: (self.last_messaged(p['id']), 1 if '@' in p['email'] else 0, p['name']),
            reverse=True
        )
        for p in profiles:
            it = QListWidgetItem()
            it.setSizeHint(QSize(0, 54))
            it.setData(Qt.ItemDataRole.UserRole, p)
            row = ProfileRow(p)
            self.profile_list.addItem(it)
            self.profile_list.setItemWidget(it, row)
            self.rows[p['id']] = row
            self.items[p['id']] = it
        self.profile_list.blockSignals(False)
        self.refresh_rows()
        if self.search.text().strip():
            self.filter_profiles(self.search.text())
        target = self.items.get(keep) if keep else None
        if target:
            self.profile_list.setCurrentItem(target)
        elif self.profile_list.count():
            self.profile_list.setCurrentRow(0)  # most recently messaged account

    def refresh_rows(self):
        ordered_ids = sorted(
            self.rows,
            key=lambda pid: (
                self.last_messaged(pid),
                1 if '@' in self.profiles_by_id[pid]['email'] else 0,
                self.profiles_by_id[pid]['name']
            ),
            reverse=True
        )
        current_ids = [
            self.profile_list.item(i).data(Qt.ItemDataRole.UserRole)['id']
            for i in range(self.profile_list.count())
        ]
        if ordered_ids != current_ids and len(ordered_ids) == len(current_ids):
            keep = self.current['id'] if self.current else None
            self.profile_list.blockSignals(True)
            self.profile_list.clear()
            self.rows.clear()
            self.items.clear()
            for pid in ordered_ids:
                p = self.profiles_by_id[pid]
                it = QListWidgetItem()
                it.setSizeHint(QSize(0, 54))
                it.setData(Qt.ItemDataRole.UserRole, p)
                row = ProfileRow(p)
                self.profile_list.addItem(it)
                self.profile_list.setItemWidget(it, row)
                self.rows[pid] = row
                self.items[pid] = it
            if keep and keep in self.items:
                self.profile_list.setCurrentItem(self.items[keep])
            self.profile_list.blockSignals(False)
            if self.search.text().strip():
                self.filter_profiles(self.search.text())

        latest = max(self.rows, key=self.last_messaged, default=None)
        for pid, row in self.rows.items():
            st = self.state.get(pid)
            lm = self.last_messaged(pid)
            row.refresh(lm, st['limit'], st['limit_until'], pid == latest and lm > 0)
            self.items[pid].setSizeHint(QSize(0, 70 if st['limit'] else 54))
        self.refresh_header()

    def refresh_header(self):
        if not self.current:
            return
        st = self.state.get(self.current['id'])
        lim = st['limit']
        self.top_avatar.setPixmap(circular_avatar(
            self.current['picture_path'], 28, self.current['name'][:1] or 'U', RED if lim else None))
        self.top_name.setStyleSheet(f'color: {RED};' if lim else '')
        self.banner.setVisible(lim)
        if lim:
            msg = fmt_reset(st['limit_until']).replace('Limit doldu · ', '')
            self.banner.setText(f'Bu hesabın mesaj limiti doldu — {msg}' if st['limit_until']
                                else 'Bu hesabın mesaj limiti doldu')

    def filter_profiles(self, q):
        q = q.lower().strip()
        for i in range(self.profile_list.count()):
            it = self.profile_list.item(i)
            p = it.data(Qt.ItemDataRole.UserRole)
            it.setHidden(q not in (p['name'] + ' ' + p['email']).lower())

    def on_profile(self, item, _prev=None):
        if not item:
            return
        prof = item.data(Qt.ItemDataRole.UserRole)
        self.current = prof
        self.top_name.setText(prof['name'])
        self.top_mail.setText(prof['email'])
        pid = prof['id']
        if pid in self.access_order:
            self.access_order.remove(pid)
        self.access_order.insert(0, pid)

        pane = self.panes.get(pid)
        if pane is None:
            pane = ChatPane(pid)
            pane.watcher.sent.connect(lambda pid=pid: self.on_sent(pid))
            pane.view.titleChanged.connect(lambda t, pid=pid: self.on_title(pid, t))
            pane.view.loadFinished.connect(lambda _ok, pid=pid: self._on_pane_loaded(pid))
            self.panes[pid] = pane
            self.stack.addWidget(pane)
        else:
            if pane.loaded and pane.view.page().lifecycleState() != QWebEnginePage.LifecycleState.Active:
                pane.view.page().setLifecycleState(QWebEnginePage.LifecycleState.Active)
            self.sync_chats_from_api(pid)
        self.stack.setCurrentWidget(pane)
        self.refresh_header()
        self.load_chats(pid)
        QTimer.singleShot(15000, self.manage_tab_memory)

    def manage_tab_memory(self):
        """Keeps active tab Active, last 3 background tabs Frozen (instant switch, 0% CPU), and older tabs Discarded (saves RAM)."""
        cur_id = self.current['id'] if self.current else None
        bg_idx = 0
        for pid in self.access_order:
            if pid == cur_id:
                continue
            pane = self.panes.get(pid)
            if not pane or not pane.loaded:
                continue
            bg_idx += 1
            page = pane.view.page()
            if bg_idx <= 3:
                if page.lifecycleState() == QWebEnginePage.LifecycleState.Active:
                    page.setLifecycleState(QWebEnginePage.LifecycleState.Frozen)
            else:
                if page.lifecycleState() != QWebEnginePage.LifecycleState.Discarded:
                    page.setLifecycleState(QWebEnginePage.LifecycleState.Discarded)

    def _on_pane_loaded(self, pid):
        QTimer.singleShot(2000, lambda: self.check_limit(pid))
        QTimer.singleShot(1500, lambda: self.sync_chats_from_api(pid))

    def load_chats(self, pid):
        self.chat_list.clear()
        merged = {}
        for c in self.manager.get_chat_titles(pid):
            merged[c['url']] = {'title': c['title'], 'ts': 0}
        for url, c in self.state.get(pid)['chats'].items():
            if url in merged and not c.get('title'):
                continue
            merged[url] = c
        order = sorted(merged.items(), key=lambda kv: kv[1].get('ts', 0), reverse=True)
        if not order:
            it = QListWidgetItem('Henüz sohbet yok')
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.chat_list.addItem(it)
            return
        for url, c in order[:60]:
            it = QListWidgetItem(c['title'])
            it.setToolTip(c['title'])
            it.setData(Qt.ItemDataRole.UserRole, url)
            self.chat_list.addItem(it)

    # ----- tracking actual messages (never tab opens or clicks) -----
    def on_sent(self, pid):
        """Triggered ONLY when a message completion POST is sent to Claude."""
        now = time.time()
        st = self.state.get(pid)
        st['last_messaged'] = now
        pane = self.panes.get(pid)
        path = pane.view.url().path() if pane else ''
        if path.startswith('/chat/'):
            key = pane.view.url().toString().split('?')[0].split('#')[0]
            if key in st['chats']:
                st['chats'][key]['ts'] = now
        else:
            self.pending_send[pid] = now
        self.state.save()
        self.refresh_rows()
        QTimer.singleShot(4000, lambda: self.check_limit(pid))

    def on_title(self, pid, title):
        """Captures conversation titles for the sidebar without falsely bumping last_messaged on tab views."""
        pane = self.panes.get(pid)
        if not pane:
            return
        url = pane.view.url()
        if not url.path().startswith('/chat/'):
            return
        t = title.strip()
        if t.endswith(' - Claude'):
            t = t[:-9]
        if not t or t == 'Claude':
            return
        key = url.toString().split('?')[0].split('#')[0]
        chats = self.state.get(pid)['chats']
        old = chats.get(key)
        ts = (old or {}).get('ts', 0)
        sent_ts = self.pending_send.pop(pid, None)
        if sent_ts:
            ts = sent_ts
        is_new = old is None or old.get('title') != t
        chats[key] = {'title': t, 'ts': ts}
        self.state.save()
        if is_new and self.current and self.current['id'] == pid:
            self.load_chats(pid)

    def sync_chats_from_api(self, pid):
        """Fetches exact conversation updated_at timestamps from Claude's API inside the logged-in session."""
        pane = self.panes.get(pid)
        if not pane or not pane.loaded:
            return
        host = pane.view.url().host()
        if not host.endswith('claude.ai'):
            return
        pane.view.page().runJavaScript(SYNC_CHATS_START_JS)
        QTimer.singleShot(1800, lambda: self._collect_synced_chats(pid))

    def _collect_synced_chats(self, pid):
        pane = self.panes.get(pid)
        if not pane or not pane.loaded:
            return
        pane.view.page().runJavaScript(
            "window.__ct_sync_res || ''",
            lambda res, pid=pid: self._apply_synced_chats(pid, res)
        )

    def _apply_synced_chats(self, pid, res):
        if not res:
            return
        try:
            items = json.loads(res)
        except Exception:
            return
        if not isinstance(items, list) or not items:
            return
        st = self.state.get(pid)
        chats = st['chats']
        max_ts = st.get('last_messaged') or 0
        changed = False
        for c in items:
            uid = c.get('uuid')
            if not uid:
                continue
            url = f'https://claude.ai/chat/{uid}'
            title = (c.get('name') or 'Adsız sohbet').strip()
            dt = self.manager.iso_to_datetime(c.get('updated_at'))
            ts = dt.timestamp() if dt else 0
            if ts > max_ts:
                max_ts = ts
            prev = chats.get(url)
            if not prev or prev.get('title') != title or abs((prev.get('ts') or 0) - ts) > 1:
                chats[url] = {'title': title, 'ts': ts}
                changed = True
        if max_ts > (st.get('last_messaged') or 0):
            st['last_messaged'] = max_ts
            changed = True
        if changed:
            self.state.save()
            self.refresh_rows()
            if self.current and self.current['id'] == pid:
                self.load_chats(pid)

    def check_limit(self, pid):
        pane = self.panes.get(pid)
        if pane and pane.loaded and pane.view.page().lifecycleState() != QWebEnginePage.LifecycleState.Discarded:
            pane.view.page().runJavaScript(LIMIT_JS, lambda res, pid=pid: self.on_limit_result(pid, res))

    def on_limit_result(self, pid, res):
        st = self.state.get(pid)
        pane = self.panes.get(pid)
        changed = False
        if res:
            self.misses[pid] = 0
            until = parse_reset_time(res) or st['limit_until']
            if not st['limit'] or st['limit_until'] != until:
                st['limit'], st['limit_until'] = True, until
                changed = True
        elif st['limit'] and pane and pane.on_chat_page():
            self.misses[pid] = self.misses.get(pid, 0) + 1
            if self.misses[pid] >= 2:  # notice gone for a while -> limit lifted
                st['limit'], st['limit_until'] = False, None
                changed = True
        if changed:
            self.state.save()
            self.refresh_rows()

    def tick(self):
        now = time.time()
        dirty = False
        for _pid, st in self.state.data.items():
            if st.get('limit') and st.get('limit_until') and st['limit_until'] <= now:
                st['limit'], st['limit_until'] = False, None
                dirty = True
        if dirty:
            self.state.save()
        for pid in list(self.rows):
            dt = self.manager.get_last_chat_time(pid)
            if dt:
                self.chrome_last_chat[pid] = dt.timestamp()
        for pid in self.panes:
            self.check_limit(pid)
        self.refresh_rows()

    # ----- actions -----
    def _pane(self):
        return self.panes.get(self.current['id']) if self.current else None

    def _open_in_current(self, url):
        p = self._pane()
        if p and url:
            p.open(url)

    def _view_call(self, name):
        p = self._pane()
        if p:
            getattr(p.view, name)()

    def open_in_chrome(self):
        if self.current:
            p = self._pane()
            url = p.view.url().toString() if p else 'https://claude.ai'
            self.manager.launch_profile(self.current['id'], url)

    # ----- system tray (gizli simgeler) -----
    def _setup_tray(self):
        pm = circular_avatar(None, 32, 'C')
        icon = QIcon(pm)
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip('Claude Oturumları')

        menu = QMenu()
        menu.setStyleSheet(
            f"QMenu {{ background: {SIDEBAR}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}"
            f"QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}"
            f"QMenu::item:selected {{ background: {SELECTED}; }}"
        )
        act_show = QAction('Aç / Göster', self)
        act_show.triggered.connect(self.restore_from_tray)
        act_purge = QAction('RAM / Önbelleği Sıfırla (Uyku Modu)', self)
        act_purge.triggered.connect(self.purge_ram)
        act_quit = QAction('Tamamen Çıkış Yap', self)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_show)
        menu.addAction(act_purge)
        menu.addSeparator()
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.restore_from_tray()

    def restore_from_tray(self):
        self._sleeping = False
        if not self.timer.isActive():
            self.timer.start(20000)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        p = self._pane()
        if p is None and self.profile_list.currentItem():
            self.on_profile(self.profile_list.currentItem())
        elif p and p.loaded and p.view.page().lifecycleState() != QWebEnginePage.LifecycleState.Active:
            p.view.page().setLifecycleState(QWebEnginePage.LifecycleState.Active)

    def purge_ram(self):
        """Destroys all WebEngine views/renderers, stops background timers, and trims Windows working set."""
        self._sleeping = True
        self.timer.stop()
        self.hide()
        for _pid, pane in list(self.panes.items()):
            try:
                pane.profile.clearHttpCache()
            except Exception:
                pass
            self.stack.removeWidget(pane)
            try:
                old_page = pane.view.page()
                pane.view.setPage(None)
                if old_page:
                    old_page.deleteLater()
                pane.view.deleteLater()
                pane.profile.deleteLater()
            except Exception:
                pass
            pane.deleteLater()
        self.panes.clear()
        self.access_order.clear()
        self.manager._idb_cache.clear()
        QTimer.singleShot(600, self._trim_working_set)
        QTimer.singleShot(1800, self._trim_working_set)
        self.tray.showMessage(
            'RAM Sıfırlandı (Uyku Modu)',
            'Tüm sekmeler ve önbellek RAM\'den boşaltıldı. Tekrar açana kadar arka planda hiçbir işlem yapılmayacak.',
            QSystemTrayIcon.MessageIcon.Information,
            3000
        )

    def _trim_working_set(self):
        gc.collect()
        try:
            k32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            h_self = k32.GetCurrentProcess()
            psapi.EmptyWorkingSet(h_self)
            k32.SetProcessWorkingSetSize(h_self, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
        except Exception:
            pass

    def quit_app(self):
        self._force_quit = True
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event):
        if not self._force_quit and self.tray.isVisible():
            event.ignore()
            self.hide()
            QTimer.singleShot(5000, self._freeze_if_hidden)
        else:
            event.accept()

    def _freeze_if_hidden(self):
        if not self.isVisible():
            p = self._pane()
            if p and p.loaded and p.view.page().lifecycleState() == QWebEnginePage.LifecycleState.Active:
                p.view.page().setLifecycleState(QWebEnginePage.LifecycleState.Frozen)


STYLE = f"""
* {{ font-family: 'Segoe UI'; color: {TEXT}; }}
QMainWindow, QStackedWidget, #empty {{ background: {BG}; }}
QDialog {{ background: {BG}; }}
#empty {{ color: {MUTED}; font-family: Georgia; font-size: 18px; }}

#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
#brand {{ font-family: Georgia; font-size: 20px; padding: 2px 4px; }}
#section {{ color: {MUTED}; font-size: 10px; font-weight: 600; letter-spacing: 1px; padding: 10px 4px 2px 4px; }}

QLineEdit {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 7px 10px; font-size: 13px; selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}

QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 8px; margin: 1px 0; color: {TEXT}; }}
QListWidget::item:hover {{ background: {HOVER}; }}
QListWidget::item:selected {{ background: {SELECTED}; }}
#chatList::item {{ padding: 7px 10px; font-size: 13px; color: #D6D3CB; }}
#chatList::item:disabled {{ color: {MUTED}; }}

#rowName {{ font-size: 13px; font-weight: 600; }}
#rowMail {{ font-size: 11px; color: {MUTED}; }}
#rowWhen {{ font-size: 11px; color: {MUTED}; }}
#rowLimit {{ font-size: 11px; color: {RED}; font-weight: 600; }}
#lastBadge {{
    background: {ACCENT}; color: #1F1E1D; font-size: 9px; font-weight: 700;
    border-radius: 4px; padding: 1px 5px;
}}

#topbar {{ background: {BG}; border-bottom: 1px solid {BORDER}; }}
#topName {{ font-family: Georgia; font-size: 16px; }}
#topMail {{ color: {MUTED}; font-size: 12px; }}
#banner {{
    background: rgba(239, 90, 80, 0.12); color: {RED}; font-size: 12px; font-weight: 600;
    padding: 7px; border-bottom: 1px solid rgba(239, 90, 80, 0.35);
}}

QPushButton {{ background: transparent; border: none; border-radius: 7px; font-size: 13px; }}
QPushButton#icon {{ min-width: 32px; min-height: 32px; font-size: 16px; color: {MUTED}; }}
QPushButton#icon:hover, QPushButton#ghost:hover {{ background: {HOVER}; color: {TEXT}; }}
QPushButton#ghost {{ padding: 6px 12px; color: {MUTED}; border: 1px solid {BORDER}; font-size: 12px; }}

#loadBar {{ background: transparent; border: none; }}
#loadBar::chunk {{ background: {ACCENT}; }}

QScrollBar:vertical {{ background: transparent; width: 6px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QToolTip {{ background: {SELECTED}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 8px; }}
"""


if __name__ == '__main__':
    os.makedirs(APP_DATA, exist_ok=True)
    sys.argv += ['--force-dark-mode']  # dark scrollbars / form controls inside web pages
    app = QApplication(sys.argv)
    app.setApplicationName('ClaudeTakipci')
    app.setQuitOnLastWindowClosed(False)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
