import itertools
import json
import math
import os
import random
import re
import threading
import time
import urllib.parse
import urllib.request
from fractions import Fraction
from html import escape as html_escape
from pathlib import Path
import streamlit as st

# 타임머신 카운트업 애니메이션 재생 시간(ms)
TIMEMACHINE_ANIM_DURATION_MS = 4500

# 앱 전체에서 공통으로 사용할 폰트 (굴림체)
APP_FONT_FAMILY = "'굴림', Gulim, sans-serif"

# ==========================================
# 방문자 수 기록 (Upstash Redis 또는 외부 TXT 파일에 저장)
#  - 누군가 앱에 접속할 때마다(새 세션이 열릴 때마다) 방문자 수를 1 늘려서 저장한다.
#  - 같은 사람이 버튼을 눌러 스크립트가 다시 실행돼도 늘지 않는다. (세션당 딱 한 번)
#    페이지를 새로고침하거나 새 탭으로 열면 새 세션이라 1 늘어난다.
#  - Upstash Redis 를 쓰면 서버가 잠들었다 깨어나거나 다시 배포돼도 숫자가 유지된다.
#    환경변수(Streamlit Cloud 는 앱 설정의 Secrets)에 UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN 을
#    넣으면 켜진다. 저장 이름은 UPSTASH_COUNTER_KEY 로 바꿀 수 있다. (기본값: visitor_count_hs)
#    숫자는 Upstash 콘솔에서 확인하거나 고친다. 숫자를 올리는 INCR 는 한 번에 처리돼서 동시 접속에도 꼬이지 않는다.
#    Upstash 를 켰는데 호출에 실패하면 번호가 뒤섞이지 않도록 그 방문은 세지 않고, 숫자도 보여 주지 않는다.
#  - Upstash 설정이 없으면(내 컴퓨터에서 실행할 때 등) TXT 파일에 저장한다.
#    저장 파일은 이 파일 옆의 visitor_count.txt 이고, 안에는 방문자 수 숫자 하나만 들어 있다.
#    메모장으로 열어 숫자를 확인하거나 고칠 수 있다. (예: 0 으로 바꾸면 처음부터 다시 센다)
#    서버를 껐다 켜도 이어서 센다. 파일 위치는 환경변수 VISITOR_COUNT_FILE 로 바꿀 수 있다.
#  - 인트로 화면의 사진 위쪽에 'N번째 방문자'로 보여 준다. 숨기려면 SHOW_VISITOR_COUNT 를 False 로 바꾼다.
#  - TXT 파일 방식은 접속이 동시에 몰려도 숫자가 꼬이지 않게 잠금(lock)을 걸고, 쓰는 도중 꺼져도 파일이 깨지지 않게
#    임시 파일에 쓴 뒤 바꿔치기(os.replace)한다. 저장에 실패해도(읽기 전용 폴더 등) 앱은 그대로 동작한다.
#    (스크립트는 접속마다 처음부터 다시 실행되므로, 잠금은 st.cache_resource 로 서버에 하나만 만들어 모두가 함께 쓴다.)
#    파일을 읽다가 일시적으로 실패하면 기록을 .broken 으로 옮기지 않고 그 방문만 세지 않는다.
#  - 주의: Streamlit Community Cloud 처럼 재배포·재시작 때 파일이 초기화되는 곳에서는 TXT 파일 기록이 초기화된다.
#    그런 곳에서는 Upstash 를 켜 두어야 숫자가 유지된다.
# ==========================================
SHOW_VISITOR_COUNT = True
VISITOR_COUNT_FILE = Path(
    os.environ.get("VISITOR_COUNT_FILE") or Path(__file__).resolve().parent / "visitor_count.txt"
)
@st.cache_resource(show_spinner=False)
def _get_visitor_lock():
    """모든 접속이 함께 쓰는 잠금을 돌려준다.

    Streamlit 은 접속(세션)마다, 그리고 버튼을 누를 때마다 이 파일을 처음부터 다시 실행한다.
    그래서 잠금을 파일 맨 위에서 threading.Lock() 으로 만들면 매번 새 잠금이 생겨 동시 접속을 막지 못한다.
    st.cache_resource 는 서버 프로세스 안에서 한 번만 만들고 계속 같은 것을 돌려준다.
    """
    return threading.Lock()


def _load_visitor_total():
    """TXT 파일에 적힌 방문자 수를 읽는다. 파일이 없거나 비어 있으면 0 에서 시작한다.

    숫자가 아닌 내용이 들어 있으면(잘못 고쳤을 때 등) 기록을 덮어써서 날리지 않도록
    옆에 .broken 으로 보관해 두고 0 에서 새로 시작한다.
    읽기만 일시적으로 막힌 경우(OSError)는 손상이 아니므로 그대로 올려 보낸다. (기록은 건드리지 않는다.)
    """
    try:
        # utf-8-sig: 메모장이 파일 맨 앞에 붙이는 BOM 이 있어도 읽을 수 있다.
        with open(VISITOR_COUNT_FILE, encoding="utf-8-sig") as f:
            text = f.read().strip()
        if not text:
            return 0
        total = int(text)
        if total < 0:
            raise ValueError("방문자 수는 0 이상이어야 합니다")
        return total
    except FileNotFoundError:
        return 0
    except ValueError:
        try:
            os.replace(VISITOR_COUNT_FILE, f"{VISITOR_COUNT_FILE}.broken")
        except OSError:
            pass
        return 0


def _secret(name):
    """환경변수에서 값을 읽고, 없으면 Streamlit Secrets 에서 읽는다. 둘 다 없으면 빈 문자열."""
    value = os.environ.get(name)
    if not value:
        try:
            value = st.secrets.get(name)
        except Exception:  # secrets.toml 이 없는 곳(내 컴퓨터 등)에서는 못 읽어도 괜찮다.
            value = None
    return str(value).strip() if value else ""


def _upstash_settings():
    """Upstash 접속 정보 (주소, 토큰, 저장 이름)를 돌려준다. 주소나 토큰이 없으면 None."""
    base = _secret("UPSTASH_REDIS_REST_URL").rstrip("/")
    token = _secret("UPSTASH_REDIS_REST_TOKEN")
    if not base or not token:
        return None
    if not base.startswith(("http://", "https://")):
        base = "https://" + base  # 주소만 붙여 넣어도 동작하게 한다.
    return base, token, _secret("UPSTASH_COUNTER_KEY") or "visitor_count_hs"


def _upstash_incr(base, token, key):
    """Upstash 에서 방문자 수를 1 올리고, 올린 뒤의 값을 돌려준다. 실패하면 None."""
    req = urllib.request.Request(
        f"{base}/incr/{urllib.parse.quote(key, safe='')}",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            total = int(json.load(resp)["result"])
    except Exception as e:
        print(f"[방문자 수] Upstash 호출에 실패했습니다: {e}")
        return None
    return total if total > 0 else None


def record_visit():
    """방문자 수를 1 늘려 저장하고, 이번 방문이 몇 번째인지 돌려준다. 저장에 실패하면 None."""
    upstash = _upstash_settings()
    if upstash is not None:
        # Upstash 를 켜 둔 경우: 실패해도 TXT 파일로 대신 세지 않는다. (두 숫자가 섞이면 번호가 뒤죽박죽이 된다.)
        return _upstash_incr(*upstash)
    with _get_visitor_lock():
        tmp_path = f"{VISITOR_COUNT_FILE}.tmp"
        try:
            total = _load_visitor_total() + 1
            VISITOR_COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(f"{total}\n")
            os.replace(tmp_path, VISITOR_COUNT_FILE)
        except OSError as e:
            print(f"[방문자 수] 읽거나 저장하지 못했습니다: {e}")
            return None
        return total


# ==========================================
# 비밀의 방
#  - 앱 안의 어떤 버튼이든(파이썬 화면의 모든 버튼 + 감옥 열쇠 퍼즐 화면의 모든 버튼)
#    누를 때마다 딱 한 번씩 2만분의 1 확률로 주사위를 굴린다.
#  - 당첨되면 그 버튼의 원래 동작은 실행되지 않고 곧바로 비밀의 방으로 이동한다.
#  - 동작을 확인해 보고 싶으면 SECRET_ROOM_ODDS 를 1 로 바꾸면 항상 이동한다.
#    (감옥 퍼즐 화면 쪽 확률은 아래 PRISON_KEY_PUZZLE_HTML 안의 SECRET_ROOM_ODDS)
# ==========================================
SECRET_ROOM_ODDS = 20_000
# 비밀의 방의 '돌아가기' 버튼은 이 비밀번호를 넣어야 눌린다. (운영팀만 풀 수 있게 잠가 둔다.)
#  - 감옥 퍼즐 화면 쪽 비밀의 방은 아래 PRISON_KEY_PUZZLE_HTML 안의 SECRET_ROOM_PASSWORD_HASH 를 쓴다.
#    (비밀번호를 바꾸면 그 해시값도 다시 계산해 넣는다. 계산 방법은 그 줄의 주석에 적혀 있다.)
SECRET_ROOM_PASSWORD = "0805"
_secret_rng = random.SystemRandom()


def go_secret_room():
    """현재 화면을 기억해 두고 비밀의 방 화면으로 넘어간다."""
    if st.session_state.step != "secret_room":
        st.session_state.secret_room_prev_step = st.session_state.step
        st.session_state.step = "secret_room"
    st.rerun()


def lucky_button(label, *args, dg=None, **kwargs):
    """st.button과 똑같이 쓰되, 클릭될 때마다 2만분의 1 확률로 비밀의 방으로 보낸다.

    dg에 st.columns의 칼럼 등을 넘기면 그 안에 버튼이 그려진다.
    """
    target = dg if dg is not None else st
    clicked = target.button(label, *args, **kwargs)
    if clicked and _secret_rng.randrange(SECRET_ROOM_ODDS) == 0:
        go_secret_room()
    return clicked


# 인트로·도착 화면: 가로 사진(1024x572)을 세로로 돌려 화면 전체에 깔고, '다음으로' 버튼은 사진 아래쪽에 둔다.
#  - 사진 카드(.photo-card)는 화면 전체를 덮고, 사진은 ::before 를 90도 돌려서 깐다. (돌린 사진은 9:16 에 가깝다.)
#    화면이 9:16 보다 가로로 넓으면(PC 등) 카드는 9:16 세로 띠로 가운데에 두고, 양옆은 화면 배경색으로 둔다.
#  - 사진 주소는 각 화면의 .xxx-card::before 에서 정한다. 사진 테두리를 잘라내고 싶으면 --photo-zoom 을 1 보다 크게 준다.
#  - 카드 안의 글씨는 <span>, 방문자 수 표시(.visitor-count)는 <div> 로 넣는다.
#  - 이 화면에는 버튼이 '다음으로' 하나뿐이므로 stButton 전체를 화면 아래 가운데에 붙인다. (폰의 홈 바 영역은 피해서 올린다.)
FULLSCREEN_PHOTO_CSS = """
/* 사진이 화면 전체를 덮으므로 스크롤 막대가 생기지 않게 한다. */
.stApp, .stMain, [data-testid="stMain"], section.main {
    overflow: hidden !important;
}
.photo-card {
    --vh: 100vh;
    --card-w: min(100vw, calc(var(--vh) * 0.5586));
    position: fixed;
    top: 0;
    left: 50%;
    z-index: 1;
    width: var(--card-w);
    height: var(--vh);
    transform: translateX(-50%);
    overflow: hidden;
    box-sizing: border-box;
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 0 28px;
    background-color: #000000;
    box-shadow: 0 0 40px rgba(0, 0, 0, 0.7);
}
@supports (height: 100dvh) {
    .photo-card { --vh: 100dvh; }
}
.photo-card::before {
    content: "";
    position: absolute;
    top: 50%;
    left: 50%;
    width: var(--vh);
    height: var(--card-w);
    transform: translate(-50%, -50%) rotate(90deg) scale(var(--photo-zoom, 1));
    background-size: cover;
    background-position: center;
    background-repeat: no-repeat;
}
.photo-card > span {
    position: relative;
    z-index: 1;
}
/* 방문자 수: 사진 위쪽 가운데에 뜨는 반투명 알약 */
.photo-card .visitor-count {
    position: absolute;
    z-index: 1;
    top: 5vh;
    left: 50%;
    transform: translateX(-50%);
    padding: 3px 11px;
    border-radius: 999px;
    background: rgba(0, 0, 0, 0.5);
    color: #f1f1f1;
    font-size: 10px !important;
    letter-spacing: 0.06em;
    white-space: nowrap;
}
.photo-card .visitor-count b {
    margin-right: 1px;
    color: #ffd27a;
    font-size: 12px;
}
div[data-testid="stButton"] {
    position: fixed !important;
    left: 50%;
    bottom: calc(env(safe-area-inset-bottom, 0px) + 7vh);
    transform: translateX(-50%);
    z-index: 2;
    width: auto !important;
}
div[data-testid="stButton"] > button {
    min-width: 168px;
}
"""


# ==========================================
# Streamlit 자체 UI 숨기기 (모든 화면 공통)
#  - 상단 툴바(Share · 즐겨찾기 · 편집 · GitHub · ⋮ 메뉴), Deploy 버튼, 실행 중 표시
#  - 우하단 'Manage app' / 뷰어 배지, 하단 푸터
#  - 메인 로직 맨 위에서 hide_streamlit_chrome() 을 한 번 부르면 모든 step 에 적용된다.
#  - 'Manage app' 은 Streamlit Cloud 가 붙이는 요소라 클래스 이름이 바뀔 수 있다.
#    그래서 CSS 와 함께, 글자('Manage app')로 찾아 숨기는 스크립트도 같이 쓴다.
# ==========================================
HIDE_STREAMLIT_CHROME_CSS = """
<style>
header[data-testid="stHeader"],
[data-testid="stHeader"],
.stAppHeader,
[data-testid="stToolbar"],
.stAppToolbar,
[data-testid="stToolbarActions"],
[data-testid="stMainMenu"],
.stMainMenu,
[data-testid="stAppDeployButton"],
.stAppDeployButton,
.stDeployButton,
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
[data-testid="stFooter"],
#MainMenu,
footer,
[data-testid="manage-app-button"],
[class*="viewerBadge"],
[class*="_terminalButton_"],
[class*="_profileContainer_"] {
    display: none !important;
    visibility: hidden !important;
}
/* 이 스타일을 담은 빈 요소가 화면 맨 위에 쓸데없는 간격을 만들지 않도록 접어 둔다.
   (:has 를 못 알아듣는 브라우저를 위해 위쪽 규칙과 일부러 따로 적었다) */
[data-testid="stElementContainer"]:has(#chrome-hider-marker),
[data-testid="element-container"]:has(#chrome-hider-marker),
.stElementContainer:has(#chrome-hider-marker),
.element-container:has(#chrome-hider-marker) {
    display: none !important;
}
</style>
<div id="chrome-hider-marker"></div>
"""

HIDE_STREAMLIT_CHROME_JS_HTML = r"""
<script>
(function () {
  // st.iframe 의 iframe 은 앱과 같은 출처라서 window.parent.document 로 앱 화면을 만질 수 있다.
  // Streamlit Cloud 가 나중에 붙이는 'Manage app' 같은 요소는 CSS 만으로는 놓칠 수 있어서,
  // 이름과 글자로 찾아 숨기고, 새로 생길 때마다 다시 숨긴다.
  var SELECTORS = [
    '[data-testid="stHeader"]', '[data-testid="stToolbar"]', '[data-testid="stToolbarActions"]',
    '[data-testid="stMainMenu"]', '[data-testid="stAppDeployButton"]', '[data-testid="stDecoration"]',
    '[data-testid="stStatusWidget"]', '[data-testid="manage-app-button"]',
    '[class*="viewerBadge"]', '[class*="_terminalButton_"]', '[class*="_profileContainer_"]'
  ];
  var LABELS = ['manage app'];

  function hide(el) {
    if (el && el.style) el.style.setProperty('display', 'none', 'important');
  }

  function textOf(el) {
    return (el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
  }

  function hasLabel(text) {
    for (var i = 0; i < LABELS.length; i++) {
      if (text.indexOf(LABELS[i]) !== -1) return true;
    }
    return false;
  }

  // 글자가 들어 있는 '가장 안쪽' 짧은 요소만 고른다. (그 글자를 품은 바깥 컨테이너가 통째로 숨겨지지 않도록)
  function isInnermostLabelElement(el) {
    var text = textOf(el);
    if (text.length > 40 || !hasLabel(text)) return false;
    if (el.querySelector('iframe')) return false;
    for (var i = 0; i < el.children.length; i++) {
      if (hasLabel(textOf(el.children[i]))) return false;
    }
    return true;
  }

  // 화면 구석에 고정(fixed)된 버튼은 그것을 감싼 고정 박스째로 숨긴다.
  function hideWithFixedAncestor(el, doc) {
    var view = doc.defaultView, target = el, node = el;
    while (node && node !== doc.body && node !== doc.documentElement) {
      if (view.getComputedStyle(node).position === 'fixed') { target = node; break; }
      node = node.parentElement;
    }
    // 앱 화면 전체를 품은 고정 박스까지 통째로 숨기는 일이 없도록 하는 안전장치
    if (target !== el && (target.querySelector('iframe') || (target.textContent || '').length > 120)) target = el;
    hide(target);
  }

  function sweep(doc) {
    try {
      SELECTORS.forEach(function (sel) {
        doc.querySelectorAll(sel).forEach(hide);
      });
      // 아이콘 글자가 앞에 붙어 있어도 잡히도록 '포함' 으로 비교한다.
      doc.querySelectorAll('button, a, div, span, p, [role="button"]').forEach(function (el) {
        if (isInnermostLabelElement(el)) hideWithFixedAncestor(el, doc);
      });
    } catch (e) { /* 접근할 수 없는 문서는 건너뛴다 */ }
  }

  function watch(doc) {
    var timer = null;
    function schedule() {
      if (timer) return;
      timer = setTimeout(function () { timer = null; sweep(doc); }, 100);
    }
    sweep(doc);
    try {
      new MutationObserver(schedule).observe(doc.documentElement, { childList: true, subtree: true });
    } catch (e) { /* 관찰할 수 없으면 한 번 훑은 것으로 끝낸다 */ }
  }

  // 앱 화면(parent)과, 접근이 허용되는 경우 그 바깥 페이지(parent.parent)를 모두 지킨다.
  var docs = [];
  try { docs.push(window.parent.document); } catch (e) {}
  try {
    if (window.parent.parent !== window.parent) docs.push(window.parent.parent.document);
  } catch (e) {}
  docs.forEach(watch);

  // 이 스크립트를 담은 빈 iframe 이 화면에 간격을 만들지 않도록 접어 둔다.
  try {
    var box = window.frameElement && window.frameElement.closest(
      '[data-testid="stElementContainer"], [data-testid="element-container"], .stElementContainer, .element-container'
    );
    if (box) {
      box.style.setProperty('position', 'absolute', 'important');
      box.style.setProperty('width', '0', 'important');
      box.style.setProperty('height', '0', 'important');
      box.style.setProperty('overflow', 'hidden', 'important');
      box.style.setProperty('margin', '0', 'important');
    }
  } catch (e) {}
})();
</script>
"""


def hide_streamlit_chrome():
    """모든 화면 공통: Streamlit 자체 버튼(상단 툴바·메뉴, Manage app 등)을 숨긴다."""
    st.markdown(HIDE_STREAMLIT_CHROME_CSS, unsafe_allow_html=True)
    # st.iframe 은 height=0 을 허용하지 않아서 가장 작은 값인 1px 로 둔다.
    st.iframe(HIDE_STREAMLIT_CHROME_JS_HTML, height=1)


# ==========================================
# 스톱워치 (난이도 선택 화면에서 난이도를 '선택'하면 시작해서, 이후 모든 화면 우측 상단에 작게 보여 준다)
#  - 분:초.밀리초 (예: 03:25.417) 로 보여 주고, 마지막 화면의 "다음으로"를 누르면 그 시간에서 멈춘다.
#  - 시작 시각은 서버(파이썬)가 기록하고, 화면은 브라우저에서 계속 올라간다. (버튼을 눌러 스크립트가 다시
#    실행돼도 끊기지 않는다.) 서버 시계와 접속한 기기의 시계를 직접 비교하지 않고 '흐른 시간'만 넘겨 준다.
#  - 숫자 상자는 앱 화면(parent)에 만들고, 멈추기·읽기는 window.parent.__sw 로 한다. (감옥 퍼즐 화면이 쓴다.)
# ==========================================
STOPWATCH_OVERLAY_HTML = r"""
<script>
(function () {
  // 앱 화면(window.parent)에 스톱워치 상자를 만들고 올리는 코드. 이 iframe 이 사라져도 계속 돌도록
  // 앱 화면 쪽에 <script> 로 심어서 실행한다. (심지 못하면 이 iframe 안에서 그대로 실행한다.)
  function install(win, doc, beforeMs) {
    if (win.__sw) return win.__sw;
    var el = doc.getElementById('stopwatch-overlay');
    if (!el) {
      el = doc.createElement('div');
      el.id = 'stopwatch-overlay';
      el.setAttribute('aria-hidden', 'true');
      // 글씨 크기는 font 의 20px. (더 키우거나 줄이려면 이 숫자만 바꾼다)
      // 처음에는 투명(opacity:0)으로 두었다가 천천히 또렷해진다. (희미하게 보이기 시작하는 효과)
      // 걸리는 시간은 아래 transition 의 1.6s 이다. 위치가 움직이는 효과가 아니라 투명도만 바뀌므로,
      // 윈도우의 '애니메이션 효과 끄기'(prefers-reduced-motion) 설정과 상관없이 항상 재생한다.
      el.style.cssText = 'position:fixed;top:10px;right:12px;z-index:2147483647;padding:3px 12px;'
        + 'border-radius:10px;background:rgba(0,0,0,.6);color:#fff;pointer-events:none;user-select:none;'
        + 'font:700 20px/1.4 Consolas,"Courier New",monospace;letter-spacing:.02em;'
        + 'font-variant-numeric:tabular-nums;text-shadow:none;'
        + 'opacity:0;transition:opacity 1.6s ease-out;';
      doc.body.appendChild(el);
      void el.offsetWidth;            // 투명한 상태를 화면에 한 번 확정한 뒤에 바꿔야 전환이 일어난다.
      el.style.opacity = '1';
    }
    var sw = { base: Date.now() - beforeMs, frozen: null };
    function pad(n, w) { n = String(n); while (n.length < w) n = '0' + n; return n; }
    function fmt(ms) {
      ms = Math.max(0, Math.floor(ms));
      return pad(Math.floor(ms / 60000), 2) + ':' + pad(Math.floor(ms / 1000) % 60, 2) + '.' + pad(ms % 1000, 3);
    }
    sw.read = function () { return sw.frozen !== null ? sw.frozen : Date.now() - sw.base; };
    function draw() { el.textContent = fmt(sw.read()); }
    function tick() { draw(); if (sw.frozen === null) win.requestAnimationFrame(tick); }
    sw.stop = function () {
      if (sw.frozen === null) { sw.frozen = Date.now() - sw.base; draw(); }
      return sw.frozen;
    };
    // 시간을 더한다. (오답 용의자를 지목했을 때의 벌칙 시간) 이미 멈춘 뒤에는 멈춘 시간에 더한다.
    sw.penalize = function (ms) {
      ms = Math.max(0, Math.floor(Number(ms) || 0));
      if (sw.frozen !== null) { sw.frozen += ms; } else { sw.base -= ms; }
      draw();
      return sw.read();
    };
    win.__sw = sw;
    tick();
    return sw;
  }

  var before = Number("__ELAPSED_BEFORE_MS__") || 0;
  var win, doc;
  try { win = window.parent; doc = win.document; } catch (e) { return; }
  if (win.__sw) return;            // 이미 돌고 있으면 시간을 다시 맞추지 않는다. (시계가 뒤로 가지 않게)
  try {
    var s = doc.createElement('script');
    s.textContent = '(' + install.toString() + ')(window, document, ' + before + ');';
    doc.head.appendChild(s);
  } catch (e) { /* 막히면 아래에서 직접 실행한다 */ }
  if (!win.__sw) { try { install(win, doc, before); } catch (e) {} }

  // 이 스크립트를 담은 빈 iframe 이 화면에 간격을 만들지 않도록 접어 둔다.
  try {
    var box = window.frameElement && window.frameElement.closest(
      '[data-testid="stElementContainer"], [data-testid="element-container"], .stElementContainer, .element-container'
    );
    if (box) {
      box.style.setProperty('position', 'absolute', 'important');
      box.style.setProperty('width', '0', 'important');
      box.style.setProperty('height', '0', 'important');
      box.style.setProperty('overflow', 'hidden', 'important');
      box.style.setProperty('margin', '0', 'important');
    }
  } catch (e) {}
})();
</script>
"""


def show_stopwatch():
    """스톱워치가 시작된 뒤(난이도를 고른 이후)에는 모든 화면에서 우측 상단에 스톱워치를 올린다."""
    if "stopwatch_start" not in st.session_state:
        return
    # 같은 내용이면 iframe 이 다시 만들어지지 않으므로, 처음 한 번만 만들어 둔다.
    if "stopwatch_html" not in st.session_state:
        elapsed_ms = max(0, round((time.monotonic() - st.session_state.stopwatch_start) * 1000))
        st.session_state.stopwatch_html = STOPWATCH_OVERLAY_HTML.replace("__ELAPSED_BEFORE_MS__", str(elapsed_ms))
    st.iframe(st.session_state.stopwatch_html, height=1)


# ==========================================
# 오답 벌칙 시간 (문제를 풀다가 틀릴 때마다 스톱워치에 시간을 더한다)
#  - 문제 풀이 화면에서 "완료 ❯" 를 눌렀는데 정답이 아니면 WRONG_ANSWER_PENALTY_SECONDS 초가 더해진다.
#    (같은 오답을 또 내도, 빈칸으로 눌러도 틀릴 때마다 더해진다.)
#  - 스톱워치는 브라우저(앱 화면)에서 돌아가므로, 틀릴 때마다 아래 작은 스크립트를 담은 iframe 을 한 번 띄워
#    window.parent.__sw.penalize() 를 부른다. 내용이 매번 달라야 iframe 이 새로 만들어지므로 틀린 횟수와
#    세션 표식을 내용에 넣고, 같은 번호는 두 번 더하지 않게 앱 화면 쪽에도 기록해 둔다.
#  - 시간을 바꾸려면 WRONG_ANSWER_PENALTY_SECONDS 만 고친다.
# ==========================================
WRONG_ANSWER_PENALTY_SECONDS = 5

WRONG_ANSWER_PENALTY_HTML = r"""
<script>
(function () {
  try {
    var win = window.parent;
    var key = "__NONCE__";
    var done = win.__swPenaltyDone || (win.__swPenaltyDone = {});
    if (!done[key] && win.__sw && typeof win.__sw.penalize === 'function') {
      done[key] = true;
      win.__sw.penalize(__PENALTY_MS__);
    }
  } catch (e) {}
  // 이 스크립트를 담은 빈 iframe 이 화면에 간격을 만들지 않도록 접어 둔다. (스톱워치 iframe 과 같은 방식)
  try {
    var box = window.frameElement && window.frameElement.closest(
      '[data-testid="stElementContainer"], [data-testid="element-container"], .stElementContainer, .element-container'
    );
    if (box) {
      box.style.setProperty('position', 'absolute', 'important');
      box.style.setProperty('width', '0', 'important');
      box.style.setProperty('height', '0', 'important');
      box.style.setProperty('overflow', 'hidden', 'important');
      box.style.setProperty('margin', '0', 'important');
    }
  } catch (e) {}
})();
</script>
"""


def add_wrong_answer_penalty():
    """문제를 틀렸을 때 한 번 부른다. 스톱워치에 WRONG_ANSWER_PENALTY_SECONDS 초를 더한다."""
    if "wrong_answer_token" not in st.session_state:
        st.session_state.wrong_answer_token = f"{random.getrandbits(40):010x}"
    count = st.session_state.get("wrong_answer_count", 0) + 1
    st.session_state.wrong_answer_count = count
    html = WRONG_ANSWER_PENALTY_HTML.replace(
        "__NONCE__", f"{st.session_state.wrong_answer_token}-{count}"
    ).replace("__PENALTY_MS__", str(WRONG_ANSWER_PENALTY_SECONDS * 1000))
    st.iframe(html, height=1)


# ==========================================
# 스토커 단서 (단서는 두 개: 녹색 장갑, 파란색 시계)
#  - 문제 선택 화면에서는 두 단서 중 '한 개'만, 무작위로 뽑아서 보여 준다.
#    (새 문제 세트를 시작할 때마다 다시 뽑는다. 어느 쪽인지는 select_clue 에 들어 있다.)
#    문제 선택 화면의 버튼 3개 중 하나가 무작위로 정해지고(한 판에 한 번만 뽑는다),
#    그 버튼을 처음 누르는 순간 반드시 검은 화면으로 넘어가 단서 문장을 보여 주고,
#    STALKER_CLUE_SECONDS 초가 지나면 문제 선택 화면으로 저절로 돌아온다.
#    (이때 버튼의 원래 동작은 실행되지 않는다. 돌아와서 다시 누르면 평소처럼 문제로 들어간다.)
#    문장 아래에는 남은 시간이 5, 4, 3, 2, 1 로 작게 줄어든다.
#  - 문제 3개를 모두 풀어야 타임머신에 갈 수 있어서 버튼 3개를 모두 누르게 되고,
#    '매우 어려움' 문제로 곧장 가도 그 전에 단서를 먼저 보여 주므로, 단서는 반드시 한 번 나온다.
#  - 나머지 한 개는 감옥 열쇠 퍼즐을 풀었을 때 "정답은 6번 열쇠입니다" 와 함께 나온다.
#    (PRISON_KEY_PUZZLE_HTML 의 승리 창. prison_clue_text() 로 문장을 넘겨 준다.)
# ==========================================
STALKER_CLUE_TEXTS = {
    "green": "스토커는 녹색 장갑을 끼고 있었다",
    "blue": "스토커는 파란색 계열의 시계를 차고 있었다",
}
STALKER_CLUE_SECONDS = 5


def pick_select_clue():
    """문제 선택 화면에서 보여 줄 단서('green' 또는 'blue')를 무작위로 하나 뽑는다."""
    return random.choice(list(STALKER_CLUE_TEXTS))


def prison_clue_text():
    """감옥 퍼즐을 풀었을 때 보여 줄 단서: 문제 선택 화면에서 보여 주지 않은 나머지 한 개."""
    shown = st.session_state.select_clue
    return next(text for key, text in STALKER_CLUE_TEXTS.items() if key != shown)


def reset_stalker_clue():
    """새 문제 세트를 시작할 때마다 단서와 단서 버튼을 새로 뽑도록 상태를 비운다."""
    st.session_state.stalker_clue_shown = False
    st.session_state.stalker_clue_button_idx = None
    st.session_state.select_clue = pick_select_clue()
    # 이미 만들어 둔 감옥 퍼즐 화면에는 이전 단서가 들어 있으므로 버린다. (다음에 열 때 새로 만든다)
    st.session_state.pop("prison_key_html", None)


def is_stalker_clue_button(idx):
    """문제 선택 화면의 idx번째(0~2) 버튼이, 아직 단서를 보여 주지 않은 '단서 버튼'이면 True."""
    count = min(3, len(st.session_state.problems))
    if count == 0:
        return False   # 학년 문제가 없으면 단서 버튼도 없다. (단서는 '매우 어려움' 문제로 들어갈 때 먼저 보여 준다.)
    if st.session_state.stalker_clue_button_idx is None:
        st.session_state.stalker_clue_button_idx = random.randrange(count)
    return (
        not st.session_state.stalker_clue_shown
        and idx == st.session_state.stalker_clue_button_idx
    )


def go_clue(text, return_step):
    """검은 단서 화면으로 넘어가 text 를 보여 주고, 끝나면 return_step 화면으로 돌아온다."""
    st.session_state.clue_text = text
    st.session_state.clue_return_step = return_step
    st.session_state.step = "stalker_clue"
    st.rerun()


def go_stalker_clue():
    """검은 단서 화면으로 넘어가 이번 판의 단서(select_clue)를 보여 준다. 한 판에 한 번만 보여 준다."""
    st.session_state.stalker_clue_shown = True
    go_clue(STALKER_CLUE_TEXTS[st.session_state.select_clue], "select_problem")


# ==========================================
# 잘 안 보이는 글씨에 흰색 상자 깔기 (공룡 배경 그림 위의 화면들)
#  - readable_box()      : 제목·안내문처럼 짧은 글을 HTML 로 넘기면 흰 상자 안에 보여 준다.
#  - readable_markdown() : 수식($...$)이 섞인 마크다운(문제·풀이)을 흰 상자 안에 보여 준다.
#  - 오답/정답 알림(st.error · st.success)과 체크박스는 위젯이라 CSS 로 흰 바탕을 깐다.
#  - 상자 모양을 바꾸고 싶으면 아래 READABLE_TEXT_CSS 의 .readable-box 만 고치면 된다.
#  - 이 CSS 는 배경 그림이 있는 화면(문제 선택·풀이·정답, 타임머신 입력)의 <style> 안에 넣어 쓴다.
#    검은 배경 화면(인트로·도착·감옥 열쇠·비밀의 방·단서 화면)에는 넣지 않는다.
# ==========================================
READABLE_TEXT_CSS = """
/* 글씨 뒤에 까는 흰색 상자 */
.readable-box {
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
    width: fit-content;
    max-width: 100%;
    box-sizing: border-box;
    margin: 0 !important;
    padding: 12px 20px !important;
    background: rgba(255, 255, 255, 0.95) !important;
    border: 1px solid rgba(0, 0, 0, 0.12);
    border-radius: 12px;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.28);
    text-align: left;
    word-break: keep-all;
    overflow-wrap: break-word;
    overflow-x: auto;
}
.readable-box:empty {
    display: none !important;
}
/* 상자 바로 아래 버튼과 간격을 띄운다. (Streamlit 마크다운 컨테이너의 margin-bottom: -1rem 이
   요소 사이 기본 간격을 없애 버려서, 따로 여백을 줘야 한다.) */
.readable-box.gap-below {
    margin-bottom: 1rem !important;
}
.readable-box > * {
    margin: 0 !important;
}
.readable-box h1, .readable-box h2, .readable-box h3,
.readable-box h4, .readable-box h5, .readable-box h6 {
    margin: 0 !important;
    padding: 0 !important;
    color: #000000 !important;
    line-height: 1.3 !important;
    word-break: keep-all !important;
    overflow-wrap: break-word !important;
}
.readable-box h2 {
    font-size: 1.9rem !important;
}
.readable-box h3 {
    font-size: 1.5rem !important;
}
.readable-box p {
    margin: 0 !important;
    padding: 0 !important;
    font-size: 1.05rem !important;
    line-height: 1.6 !important;
    word-break: keep-all !important;
    overflow-wrap: break-word !important;
}
.readable-box hr {
    width: 100%;
    height: 0;
    margin: 0 !important;
    border: 0 !important;
    border-top: 1px solid rgba(0, 0, 0, 0.22) !important;
}
.readable-box .katex-display {
    margin: 0.2em 0 !important;
}

/* 오답·정답 알림 상자: 흰 바탕을 깔아 글씨가 또렷하게 보이게 한다. */
div[data-testid="stAlert"] {
    background-color: #ffffff !important;
    border-radius: 0.5rem !important;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.25);
}

/* 개발자 옵션 체크박스: 눈에 띄지 않게 매우 희미하게 보이도록 한다. (opacity 를 높이면 또렷해진다.) */
div[data-testid="stCheckbox"] {
    width: fit-content !important;
    max-width: 100%;
    box-sizing: border-box;
    padding: 8px 16px !important;
    background: rgba(255, 255, 255, 0.95) !important;
    border: 1px solid rgba(0, 0, 0, 0.12);
    border-radius: 12px;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.28);
    opacity: 0.15;
}
"""


def readable_box(inner_html, gap_below=False):
    """짧은 글(제목·안내문 등)을 흰색 상자 안에 보여 준다. inner_html 은 HTML 이다.

    HTML 안에 빈 줄이 끼면 상자가 끊기므로, 공백·줄바꿈은 한 칸으로 합쳐서 넘긴다.
    gap_below=True 면 상자 아래에 여백을 두어, 바로 밑의 버튼과 붙지 않게 한다.
    """
    one_line = " ".join(str(inner_html).split())
    css_class = "readable-box gap-below" if gap_below else "readable-box"
    st.markdown(
        f'<div class="{css_class}">{one_line}</div>',
        unsafe_allow_html=True,
    )


def readable_markdown(md_text):
    """수식($...$)이 섞인 마크다운을 흰색 상자 안에 보여 준다.

    상자 태그와 내용 사이에 빈 줄을 둬야 상자 안에서도 마크다운·수식이 해석된다.
    """
    st.markdown(
        f'<div class="readable-box">\n\n{md_text}\n\n</div>',
        unsafe_allow_html=True,
    )


# ==========================================
# 타임머신 이후 화면에 삽입되는 미니게임: 4x4 나무 파이프 슬라이딩 퍼즐
# (원본 게임의 버그 3건을 수정한 버전)
#  1) 캔버스 리사이즈 시 ctx.scale 누적되던 문제 수정
#  2) 더블클릭 시 회전이 3번 일어나던 문제 수정 (dblclick 리스너 제거)
#  3) BLOCK(나무 블록) 장애물이 잘못 이동 가능하던 문제 수정
# ==========================================
# ==========================================
# 감옥 열쇠 퍼즐 (7개의 열쇠 중 하나만 정답, 파이프 퍼즐을 풀어야 시도 가능)
#  - 이 화면/게임은 하나의 자체완결형 HTML로 구성되어 있으며,
#    '퍼즐을 실제로 풀고 감옥으로 돌아가야만 열쇠 잠금이 풀리는' 로직이
#    모두 이 HTML 내부의 자바스크립트에서 처리됩니다.
#    (st.iframe은 단방향 임베드라 Streamlit과 실시간으로 값을 주고받을
#     수 없기 때문에, 잠금/해제 판정을 페이지 내부에서 자체적으로 완결시켰습니다.)
# ==========================================
PRISON_KEY_PUZZLE_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>감옥 열쇠 퍼즐</title>

<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=Noto+Sans+KR:wght@400;600;800&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/canvas-confetti@1.6.0/dist/confetti.browser.min.js"></script>

<style>
  :root{
    color-scheme: dark;
    --wood-dark:#2b1810;
    --wood-mid:#4a2c1d;
    --wood-light:#8c5a3c;
    --wood-bevel:#a6724e;
    --gold:#d4af37;
    --gold-bright:#fce080;
    --emerald:#2ecc71;
    --parchment:#f3e9dc;
    --bg:#120c09;
    --danger:#e2725b;
  }
  html,body{height:100%;margin:0;}
  body{
    font-family:'Noto Sans KR','Outfit',sans-serif;
    background:#120c09 url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%232b1810' fill-opacity='0.4'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
    color:var(--parchment);
    -webkit-user-select:none; user-select:none;
    touch-action:manipulation;
    overflow-x:hidden;
  }
  .cinzel-font{ font-family:'Cinzel', serif; }
  [hidden]{ display:none !important; }
  /* Tailwind CDN이 어떤 이유로든 로드되지 못해도 모달/화면 숨김이 깨지지 않도록
     하는 안전망(핵심 표시 여부는 CDN 유틸리티 클래스에만 의존하지 않는다) */
  .hidden{ display:none !important; }
  #modalVictory:not(.hidden){
    position:fixed !important; inset:0 !important; z-index:999 !important;
    display:flex !important; align-items:center !important; justify-content:center !important;
  }

  /* ---------- shared wood/gold UI language ---------- */
  .wood-frame{
    background:linear-gradient(135deg,#3d2317 0%,#1a0d08 100%);
    border:12px solid #4a2d1d;
    border-image:linear-gradient(to bottom right,#8a5335,#2b170c,#5c3520) 12;
    box-shadow:0 20px 50px rgba(0,0,0,.8), inset 0 0 15px rgba(0,0,0,.9);
    border-radius:20px;
  }
  .btn-gold{
    background:linear-gradient(180deg,#fce080 0%,#d4af37 50%,#996515 100%);
    color:#1a0d08; font-weight:800;
    text-shadow:0 1px 0 rgba(255,255,255,.4);
    box-shadow:0 4px 10px rgba(0,0,0,.4), inset 0 1px 0 rgba(255,255,255,.6);
    border:1px solid #b8860b;
    transition:all .15s ease-in-out;
  }
  .btn-gold:hover{ transform:translateY(-2px); box-shadow:0 6px 15px rgba(212,175,55,.4), inset 0 1px 0 rgba(255,255,255,.8); filter:brightness(1.1); }
  .btn-gold:active{ transform:translateY(1px); box-shadow:0 2px 5px rgba(0,0,0,.4); }
  .btn-wood{
    background:linear-gradient(180deg,#6e422b 0%,#422517 100%);
    color:#fce080; border:1px solid #8c5a3c;
    box-shadow:0 4px 8px rgba(0,0,0,.5), inset 0 1px 0 rgba(255,255,255,.1);
    transition:all .15s ease;
  }
  .btn-wood:hover{ background:linear-gradient(180deg,#824f34 0%,#522f1d 100%); transform:translateY(-2px); color:#fff; }
  .btn-wood:active{ transform:translateY(1px); }
  .mode-toggle{ background:#1a0e08; border:2px solid #5c3826; border-radius:30px; padding:4px; }
  .mode-option{ border-radius:20px; padding:6px 14px; font-weight:700; font-size:.8rem; cursor:pointer; transition:all .2s ease; }
  .mode-option.active{ background:linear-gradient(180deg,#2ecc71 0%,#1e8449 100%); color:#fff; box-shadow:0 2px 8px rgba(46,204,113,.4); }
  .hud-panel{ background:rgba(26,14,8,.85); backdrop-filter:blur(8px); border:1px solid rgba(140,90,60,.4); box-shadow:inset 0 0 10px rgba(0,0,0,.5); }
  ::-webkit-scrollbar{ width:8px; }
  ::-webkit-scrollbar-track{ background:#1a0d08; }
  ::-webkit-scrollbar-thumb{ background:#5c3826; border-radius:4px; }

  /* ---------- prison / key-select screen ---------- */
  #prisonView{ position:fixed; inset:0; overflow-x:hidden; overflow-y:auto; display:flex; align-items:flex-start; justify-content:center; }
  #prisonView .prison-bg, #prisonView .prison-vignette, #prisonView .prison-bars, #prisonView .prison-glow{ position:fixed; }
  .prison-bg{
    position:absolute; inset:0;
    background:
      repeating-linear-gradient(90deg, rgba(0,0,0,.18) 0 3px, transparent 3px 96px),
      linear-gradient(180deg,#3a2313 0%, #241407 55%, #170d06 100%);
  }
  .prison-bg::before{ /* plank seams */
    content:""; position:absolute; inset:0;
    background-image: repeating-linear-gradient(90deg, rgba(20,10,5,.5) 0 2px, transparent 2px 96px);
    mix-blend-mode:multiply;
  }
  .prison-bg::after{ /* grain */
    content:""; position:absolute; inset:0; opacity:.5;
    background-image:url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23000' fill-opacity='0.35'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
  }
  .prison-glow{ position:absolute; width:38vmax; height:38vmax; border-radius:50%; filter:blur(60px); opacity:.35; pointer-events:none; animation:flicker 5s ease-in-out infinite; }
  .prison-glow.left{ top:-10%; left:-14%; background:radial-gradient(circle, #ff9d42, transparent 70%); animation-delay:.3s; }
  .prison-glow.right{ bottom:-14%; right:-12%; background:radial-gradient(circle, #ff7a2e, transparent 70%); animation-delay:1.1s; }
  @keyframes flicker{ 0%,100%{ opacity:.28; } 50%{ opacity:.42; } }
  .prison-vignette{ position:absolute; inset:0; background:radial-gradient(ellipse at center, transparent 32%, rgba(8,5,3,.68) 100%); }
  .prison-bars{ position:absolute; inset:0; opacity:.16; pointer-events:none; }
  .prison-bars .bar{ position:absolute; top:0; bottom:0; width:14px; background:linear-gradient(90deg,#0a0603,#5c4530 45%,#0a0603); }

  .prison-content{
    position:relative; z-index:2; width:100%; max-width:420px; margin:auto;
    display:flex; flex-direction:column; align-items:center; gap:16px;
    padding:28px 20px 28px;
  }
  .prison-head{ text-align:center; }
  .prison-kicker{ font-size:.78rem; letter-spacing:.16em; color:#c9a877; margin-bottom:2px; }
  .prison-title{ font-size:1.9rem; letter-spacing:.04em; color:var(--gold-bright); text-shadow:0 3px 10px rgba(0,0,0,.7); text-align:center; }
  .prison-sub{ font-size:.8rem; color:#c9a877; text-align:center; letter-spacing:.03em; }

  /* 안내 카드: 이야기 + 해야 할 일 + 게임 버튼 */
  .prison-quest{
    width:100%; padding:16px 16px 16px; border-radius:14px;
    background:rgba(20,12,8,.62); border:1px solid rgba(212,175,55,.35);
    box-shadow:inset 0 0 18px rgba(0,0,0,.45), 0 8px 24px rgba(0,0,0,.35);
    display:flex; flex-direction:column; gap:12px;
  }
  .quest-story{ margin:0; font-size:.88rem; line-height:1.8; color:#f3e9dc; text-align:center; word-break:keep-all; }
  .quest-story b{ color:var(--gold-bright); }
  .quest-story span{ display:block; text-wrap:balance; }
  .quest-story span + span{ margin-top:4px; }
  .quest-steps{ list-style:none; margin:0; padding:12px 0 0; border-top:1px dashed rgba(212,175,55,.3); display:flex; flex-direction:column; gap:8px; }
  .quest-steps li{ display:flex; gap:10px; align-items:flex-start; font-size:.82rem; line-height:1.55; color:#e7cfa8; word-break:keep-all; }
  .step-no{
    flex:none; width:20px; height:20px; margin-top:1px; border-radius:50%;
    background:rgba(212,175,55,.16); border:1px solid rgba(212,175,55,.6);
    color:var(--gold-bright); font-size:.72rem; font-weight:800;
    display:inline-flex; align-items:center; justify-content:center;
  }
  .quest-steps li.done{ color:#9fdcb4; }
  .quest-steps li.done .step-no{ background:var(--emerald); border-color:var(--emerald); color:#0c2a17; font-size:0; }
  .quest-steps li.done .step-no::after{ content:"✓"; font-size:.78rem; }
  .rack-caption{ width:100%; text-align:center; font-size:.8rem; color:#c9a877; letter-spacing:.03em; margin-top:2px; word-break:keep-all; text-wrap:balance; }

  .key-rack{ display:flex; flex-direction:column; gap:8px; width:100%; }
  .key-btn{
    position:relative; width:100%; height:48px; padding:0 14px;
    display:flex; align-items:center; gap:14px;
    background:rgba(20,12,8,.55); border:1px solid rgba(140,90,60,.45); border-radius:12px;
    cursor:pointer; overflow:visible; transition:transform .15s ease, border-color .15s ease, background .15s ease;
  }
  .key-btn:hover, .key-btn:focus-visible{ transform:translateX(4px); border-color:var(--gold); background:rgba(30,18,10,.72); z-index:5; outline:none; }
  .key-btn:active{ transform:translateX(1px) scale(.99); }
  /* 안티앨리어싱: 열쇠·용의자·자물쇠 SVG 는 가장자리를 정밀하게 부드럽게 그린다. */
  .key-svg, .suspect-svg, .lock-svg{ shape-rendering:geometricPrecision; text-rendering:geometricPrecision; }
  .key-svg{ width:34px; height:34px; flex:none; filter:drop-shadow(0 2px 4px rgba(0,0,0,.6)); transition:transform .15s ease; }
  .key-btn:hover .key-svg{ transform:rotate(-8deg) scale(1.08); }
  .key-label{ font-size:.85rem; color:#e7cfa8; letter-spacing:.04em; }
  .key-lock-badge{ margin-left:auto; font-size:1rem; opacity:0; transition:opacity .15s ease; }

  .key-rack.locked .key-btn{ cursor:not-allowed; opacity:.42; filter:grayscale(.55); }
  .key-rack.locked .key-btn:hover, .key-rack.locked .key-btn:focus-visible{ transform:none; border-color:rgba(140,90,60,.45); background:rgba(20,12,8,.55); }
  .key-rack.locked .key-btn:hover .key-svg{ transform:none; }
  .key-rack.locked .key-lock-badge{ opacity:.85; }

  .go-game-btn{
    width:100%; padding:12px 20px; border-radius:12px; font-size:.92rem;
    display:inline-flex; align-items:center; justify-content:center; gap:8px;
  }
  .go-game-btn:hover{ transform:translateY(-2px); }
  .go-game-btn:active{ transform:translateY(1px); }

  #toast{
    position:fixed; left:50%; bottom:28px; transform:translate(-50%,16px);
    background:rgba(18,11,7,.96); border:1px solid rgba(212,175,55,.5); color:var(--parchment);
    padding:12px 22px; border-radius:12px; font-weight:700; font-size:.85rem;
    opacity:0; pointer-events:none; transition:opacity .25s ease, transform .25s ease; z-index:500;
    box-shadow:0 12px 30px rgba(0,0,0,.55); white-space:nowrap;
  }
  #toast.show{ opacity:1; transform:translate(-50%,0); }
  #toast.success{ border-color:var(--emerald); color:#d7f8e3; }
  #toast.fail{ border-color:var(--danger); color:#fbe3dd; }

  /* ---------- 용의자 화면 ---------- */
  #suspectsView{ position:fixed; inset:0; overflow:hidden; display:flex; align-items:center; justify-content:center; }
  /* 제목은 '단서를 외워오셨나요?' 상자 바로 위에 붙여 둔다. */
  .suspects-title{
    width:100%; margin:0 0 14px; padding:0;
    font-size:1.5rem; line-height:1.4; letter-spacing:.02em; color:var(--gold-bright);
    text-shadow:0 3px 10px rgba(0,0,0,.7); text-align:center;
  }
  .suspects-content{
    position:relative; z-index:2; width:100%; max-width:500px;
    display:flex; flex-direction:column; align-items:center;
    padding:32px 20px;
  }
  /* 단서 확인 질문: 단서 자체는 이 화면에서 보여 주지 않고, 외워 왔는지만 묻는다. */
  .clue-box{
    width:100%; text-align:left; margin-bottom:20px; box-sizing:border-box;
    background:rgba(8,5,3,.72);
    border:1px solid rgba(212,175,55,.5); border-radius:14px;
    padding:16px 18px; box-shadow:inset 0 0 14px rgba(0,0,0,.5), 0 6px 18px rgba(0,0,0,.35);
  }
  .clue-question{
    text-align:center; font-size:1.05rem; font-weight:800; line-height:1.5;
    letter-spacing:.02em; color:var(--gold-bright); word-break:keep-all;
  }
  /* 폰처럼 좁은 화면: 상자 안쪽 여백을 줄인다. */
  @media (max-width:400px){
    .clue-box{ padding:14px 12px; }
  }
  .suspect-row{ display:flex; justify-content:center; gap:16px; width:100%; }
  /* min-width:0 + 그림 폭을 칸 폭에 맞춰 줄이기: 이게 없으면 그림(82px)보다 좁게 줄어들지 못해서
     좁은 화면에서 카드가 화면 밖으로 잘린다. */
  .suspect-btn{
    flex:1; min-width:0; max-width:150px; display:flex; flex-direction:column; align-items:center; gap:8px;
    background:rgba(20,12,8,.55); border:1px solid rgba(140,90,60,.45); border-radius:16px;
    padding:22px 10px 16px; cursor:pointer; transition:transform .15s ease, border-color .15s ease, background .15s ease;
  }
  .suspect-btn:hover, .suspect-btn:focus-visible{ transform:translateY(-4px); border-color:var(--gold); background:rgba(30,18,10,.72); outline:none; }
  .suspect-btn:active{ transform:translateY(-1px) scale(.98); }
  .suspect-svg{ width:min(82px, 100%); height:auto; filter:drop-shadow(0 4px 8px rgba(0,0,0,.6)); margin-bottom:4px; }
  .suspect-id{ font-size:.98rem; color:#e7cfa8; letter-spacing:.03em; font-weight:800; }
  /* 버튼 하단 중앙: 그림에서 어디가 왼쪽이고 오른쪽인지 알려 주는 표시 (보는 사람 기준) */
  .suspect-side{
    display:flex; align-items:center; justify-content:center; gap:7px; white-space:nowrap;
    font-size:.8rem; font-weight:700; color:#cdb283; letter-spacing:.02em;
  }
  .suspect-side i{ width:1px; height:12px; background:rgba(205,178,131,.6); }
  /* 폰처럼 좁은 화면: 양옆 여백과 카드 사이 간격을 줄여서 카드 3장이 한 줄에 다 들어오게 한다. */
  @media (max-width:400px){
    .suspects-content{ padding-left:10px; padding-right:10px; }
    .suspect-row{ gap:8px; }
    .suspect-btn{ padding:20px 4px 14px; }
  }
  @media (max-width:340px){
    .suspect-id{ font-size:.9rem; }
    .suspect-side{ font-size:.7rem; gap:5px; }
  }

  /* ---------- 스토커를 찾은 뒤 화면 (용의자 화면과 같은 나무 벽 배경) ---------- */
  #foundView{ position:fixed; inset:0; overflow:hidden; display:flex; align-items:center; justify-content:center; }
  .found-text{
    position:relative; z-index:2; max-width:680px; margin:0; padding:0 24px;
    font-size:clamp(1.05rem, 4.6vw, 1.5rem); line-height:1.8; letter-spacing:.02em;
    color:var(--gold-bright); text-shadow:0 3px 10px rgba(0,0,0,.7);
    text-align:center; word-break:keep-all; text-wrap:balance;
  }
  /* 우측 하단 '다음으로' 버튼: 오른쪽 벽(화면 끝)에서 여백을 둔다. */
  .found-actions{
    position:absolute; z-index:3;
    right:max(24px, env(safe-area-inset-right)); bottom:max(24px, env(safe-area-inset-bottom));
  }
  .found-next{ padding:11px 24px; border-radius:12px; font-size:.9rem; font-weight:800; cursor:pointer; }

  /* ---------- 마지막 화면: 걸린 시간 안내 (스토커를 찾은 화면과 같은 배경) ---------- */
  #finishView{ position:fixed; inset:0; overflow:hidden; display:flex; align-items:center; justify-content:center; }
  .finish-box{ position:relative; z-index:2; max-width:680px; padding:0 24px; text-align:center; }
  .finish-time{
    margin:0 0 18px; font-size:clamp(1.15rem, 5vw, 1.7rem); line-height:1.7; letter-spacing:.02em;
    color:var(--gold-bright); text-shadow:0 3px 10px rgba(0,0,0,.7); word-break:keep-all; text-wrap:balance;
  }
  .finish-note{ margin:0; font-size:clamp(.95rem, 3.8vw, 1.15rem); color:#c9a877; text-shadow:0 2px 8px rgba(0,0,0,.7); }

  /* ---------- 자물쇠 해제 연출 ---------- */
  .unlock-scene{ position:relative; height:150px; display:flex; align-items:center; justify-content:center; margin-bottom:16px; }
  .lock-svg{ width:92px; height:115px; filter:drop-shadow(0 6px 14px rgba(0,0,0,.6)); position:relative; z-index:2; }
  .lock-svg .lock-shackle{ transform-origin:60px 66px; transition:transform .5s cubic-bezier(.34,1.56,.64,1); }
  .lock-svg.shackle-open .lock-shackle{ transform:translateY(-15px) rotate(-24deg); }
  .lock-svg .lock-body{ transition:filter .3s ease; }
  .lock-svg.shackle-open .lock-body{ filter:drop-shadow(0 0 12px rgba(46,204,113,.8)); }

  .unlock-key-wrap{
    position:absolute; left:50%; top:50%; width:30px; height:75px;
    margin-left:-15px; margin-top:-56px;
    transform:translateY(-42px) rotate(-18deg); transform-origin:50% 88%;
    z-index:3; pointer-events:none;
  }
  .unlock-key-wrap .key-svg{ width:100%; height:100%; filter:drop-shadow(0 4px 8px rgba(0,0,0,.6)); }
  .unlock-key-wrap.key-turn{ animation:keyInsertTurn .65s cubic-bezier(.45,0,.4,1) forwards; }
  @keyframes keyInsertTurn{
    0%{ transform:translateY(-42px) rotate(-18deg); }
    55%{ transform:translateY(0px) rotate(0deg); }
    100%{ transform:translateY(0px) rotate(46deg); }
  }

  .unlock-glow{
    position:absolute; left:50%; top:50%; width:150px; height:150px; margin:-75px 0 0 -75px;
    border-radius:50%; background:radial-gradient(circle, rgba(46,204,113,.55), transparent 70%);
    opacity:0; pointer-events:none; z-index:1;
  }
  .unlock-glow.pulse{ animation:glowPulse .9s ease-out; }
  @keyframes glowPulse{
    0%{ opacity:0; transform:scale(.6); }
    35%{ opacity:1; transform:scale(1.1); }
    100%{ opacity:0; transform:scale(1.35); }
  }

  .unlock-text{ opacity:0; transform:translateY(8px); transition:opacity .4s ease, transform .4s ease; margin-bottom:18px; }
  .unlock-text.show{ opacity:1; transform:translateY(0); }
  .unlock-title{ font-size:1.35rem; font-weight:800; color:#8ef0b0; text-shadow:0 2px 8px rgba(0,0,0,.6); margin-bottom:6px; }
  .unlock-sub{ font-size:.8rem; color:#d8c6a8; line-height:1.5; }

  #unlockCard{ opacity:0; transform:scale(.92) translateY(10px); }
  #unlockCard.pop-in{ animation:cardPopIn .4s cubic-bezier(.34,1.56,.64,1) forwards; }
  @keyframes cardPopIn{ to{ opacity:1; transform:scale(1) translateY(0); } }

  #unlockModal:not(.hidden){
    position:fixed !important; inset:0 !important; z-index:1000 !important;
    display:flex !important; align-items:center !important; justify-content:center !important;
  }

  /* ---------- 비밀의 방 (아무 버튼이든 2만분의 1 확률로 열린다) ---------- */
  #secretRoom{
    position:fixed; inset:0; z-index:2000;
    display:flex; flex-direction:column; align-items:center; justify-content:center; gap:18px;
    padding:24px; text-align:center; color:var(--parchment);
    background:radial-gradient(ellipse at 50% 30%, #4a2a12 0%, #1a0e07 55%, #0a0604 100%);
  }
  #secretRoom .sr-title{ position:relative; top:-30px; font-size:2rem; letter-spacing:.2em; color:var(--gold-bright); text-shadow:0 0 22px rgba(252,224,128,.55); }
  #secretRoom .sr-text{ font-size:.95rem; line-height:1.8; word-break:keep-all; }
  #secretRoom .sr-pass{ width:9rem; padding:6px 12px; border-radius:8px; text-align:center; font-size:.85rem; color:var(--parchment); background:rgba(255,255,255,.08); border:1px solid rgba(252,224,128,.35); outline:none; }
  #secretRoom .sr-pass:focus{ border-color:var(--gold-bright); }
  #secretRoom .sr-pass::placeholder{ color:rgba(243,233,220,.45); }
  #secretRoom #btnSecretBack:disabled{ opacity:.35; cursor:not-allowed; transform:none !important; filter:none !important; }

  /* ---------- game screen ---------- */
  #gameView{ position:relative; min-height:100%; padding:20px 12px 28px; display:flex; flex-direction:column; align-items:center; }
  .game-topbar{ width:100%; max-width:576px; margin:0 auto 12px; }
  .back-btn{ padding:9px 16px; border-radius:10px; font-size:.8rem; font-weight:700; display:inline-flex; align-items:center; gap:8px; }
  main.game-main{ width:100%; max-width:576px; display:flex; flex-direction:column; align-items:center; }
</style>
</head>
<body>

<!-- ============================================================ -->
<!-- 감옥 열쇠 화면                                                  -->
<!-- ============================================================ -->
<div id="prisonView">
  <div class="prison-bg"></div>
  <div class="prison-bars" id="prisonBars"></div>
  <div class="prison-glow left"></div>
  <div class="prison-glow right"></div>
  <div class="prison-vignette"></div>

  <div class="prison-content">
    <div class="prison-head">
      <div class="prison-kicker"><span aria-hidden="true">🔑</span> 마지막 관문</div>
      <div class="prison-title cinzel-font">탈옥의 열쇠</div>
    </div>

    <div class="prison-quest">
      <p class="quest-story"><span>우물의 물길을 고친 뒤 정신을 차려보니, <b>낯선 감옥</b>에 갇혀 있다.</span><span>일곱 개의 열쇠가 걸려 있지만 아직은 만질 수조차 없다.</span></p>
      <ol class="quest-steps">
        <li id="questStep1"><span class="step-no">1</span><span>게임 화면의 <b>파이프 퍼즐</b>을 끝까지 연결해 물길을 완성하세요</span></li>
        <li id="questStep2"><span class="step-no">2</span><span>그 상태로 감옥에 돌아와 <b>열쇠</b>를 시도해 보세요</span></li>
      </ol>
      <button id="btnGoGame" class="btn-gold go-game-btn">
        <span aria-hidden="true">🧩</span> 게임 하러 가기
      </button>
    </div>

    <div class="rack-caption prison-sub" id="prisonSub">일곱 개의 열쇠 중 자물쇠를 여는 열쇠는 단 하나뿐이에요</div>
    <div class="key-rack" id="keyRack"></div>
  </div>
</div>

<!-- ============================================================ -->
<!-- 용의자 화면 (6번 열쇠로 자물쇠를 풀고 "계속하기"를 누르면 등장)        -->
<!-- ============================================================ -->
<div id="suspectsView" hidden>
  <div class="prison-bg"></div>
  <div class="prison-vignette"></div>
  <div class="suspects-content">
    <div class="suspects-title cinzel-font">용의자 3명 중<br>스토커를 찾아라</div>
    <div class="clue-box">
      <div class="clue-question">단서를 외워오셨나요?</div>
    </div>
    <div class="suspect-row" id="suspectRow"></div>
  </div>
</div>

<!-- ============================================================ -->
<!-- 스토커를 찾은 뒤 화면 (용의자 B 를 지목하면 등장)                    -->
<!-- ============================================================ -->
<div id="foundView" hidden>
  <div class="prison-bg"></div>
  <div class="prison-vignette"></div>
  <p class="found-text">당신은 우주대스타를 납치한 스토커를 찾았습니다!<br>자, 이제 현상금을 받으러 가볼까요?</p>
  <div class="found-actions">
    <button id="btnFoundNext" class="btn-gold found-next">다음으로</button>
  </div>
</div>

<!-- ============================================================ -->
<!-- 마지막 화면 ("다음으로"를 누르면 등장): 난이도를 고른 때부터 지금까지 걸린 시간          -->
<!-- ============================================================ -->
<div id="finishView" hidden>
  <div class="prison-bg"></div>
  <div class="prison-vignette"></div>
  <div class="finish-box">
    <p class="finish-time" id="finishTime"></p>
    <p class="finish-note">본 창은 닫으셔도 됩니다!</p>
  </div>
</div>

<!-- ============================================================ -->
<!-- 파이프 퍼즐 게임 화면                                            -->
<!-- ============================================================ -->
<div id="gameView" hidden>
  <div class="game-topbar">
    <button id="btnReturnToJail" class="btn-wood back-btn">
      <span aria-hidden="true">←</span> 감옥으로 돌아가기
    </button>
  </div>

  <main class="game-main">
    <!-- HUD Control Bar -->
    <div class="w-full hud-panel rounded-2xl p-3 mb-3 flex items-center justify-between shadow-xl flex-wrap gap-y-2">
      <div class="flex items-center space-x-2 sm:space-x-3">
        <div class="text-center">
          <span class="text-[10px] uppercase tracking-widest text-amber-500 font-bold block">난이도 / 레벨</span>
          <span id="txtLevelNum" class="text-xs sm:text-base font-extrabold text-amber-100">4x4 - 스테이지 1</span>
        </div>
        <div class="h-8 w-px bg-amber-900/50"></div>
        <div class="text-center">
          <span class="text-[10px] uppercase tracking-widest text-amber-500 font-bold block">파이프 상태</span>
          <span id="txtPipeStatus" class="text-xs sm:text-sm font-extrabold text-red-400">🔴 끊어짐</span>
        </div>
        <div class="h-8 w-px bg-amber-900/50"></div>
        <div class="text-center">
          <span class="text-[10px] uppercase tracking-widest text-amber-500 font-bold block">이동 횟수</span>
          <span id="txtMoves" class="text-lg font-extrabold text-white">0</span>
        </div>
        <div class="h-8 w-px bg-amber-900/50"></div>
        <div class="text-center">
          <span class="text-[10px] uppercase tracking-widest text-amber-500 font-bold block">경과 시간</span>
          <span id="txtTimer" class="text-lg font-extrabold text-amber-200">00:00</span>
        </div>
      </div>
      <div class="flex items-center space-x-1 text-amber-400 text-sm" id="starContainer">
        <span aria-hidden="true">★★★</span>
      </div>
    </div>

    <!-- Mode Toggle -->
    <div class="flex items-center justify-between w-full mb-3 px-1 flex-wrap gap-y-2">
      <div class="mode-toggle flex items-center space-x-1">
        <div id="modeSlide" class="mode-option active"><span aria-hidden="true">👆</span> 타일 이동</div>
        <div id="modeRotate" class="mode-option"><span aria-hidden="true">🔁</span> 회전</div>
      </div>
      <div class="flex items-center space-x-2">
        <button id="btnNewPuzzle" class="btn-wood px-3 h-9 rounded-xl text-xs font-bold flex items-center space-x-1" title="새 퍼즐">
          <span aria-hidden="true">🔀</span><span>새 퍼즐</span>
        </button>
        <button id="btnUndo" class="btn-wood px-3 h-9 rounded-xl text-xs font-bold flex items-center space-x-1" title="되돌리기">
          <span aria-hidden="true">↩️</span><span>실행 취소</span>
        </button>
        <button id="btnReset" class="btn-wood px-3 h-9 rounded-xl text-xs font-bold flex items-center space-x-1" title="초기화">
          <span aria-hidden="true">🔄</span><span>재시작</span>
        </button>
      </div>
    </div>

    <!-- Canvas -->
    <div class="wood-frame p-2 sm:p-3 relative w-full flex justify-center items-center overflow-hidden">
      <canvas id="gameCanvas" class="rounded-xl shadow-2xl cursor-pointer touch-none"></canvas>
      <div id="waterStatusBanner" class="absolute top-4 bg-emerald-900/90 border border-emerald-400 text-emerald-100 text-xs sm:text-sm px-4 py-1.5 rounded-full font-bold shadow-2xl transition-all duration-300 opacity-0 pointer-events-none transform -translate-y-4">
        <span aria-hidden="true" class="text-emerald-400 animate-bounce mr-1.5" style="display:inline-block;">💧</span>
        <span id="waterStatusText">수돗물이 흐르는 중...</span>
      </div>
    </div>
  </main>

  <!-- Victory Modal -->
  <div id="modalVictory" class="fixed inset-0 bg-black/80 backdrop-blur-md flex items-center justify-center p-4 z-50 hidden transition-opacity duration-300">
    <div class="wood-frame max-w-sm w-full p-6 text-center" id="victoryCard">
      <div class="text-xl sm:text-2xl font-extrabold text-amber-300 mb-5 tracking-wide">🔑 정답은 6번 열쇠입니다</div>
      <div id="victoryClue" style="margin:0 0 20px;padding:10px 12px;border:1px solid rgba(252,224,128,.35);border-radius:12px;background:rgba(0,0,0,.35);color:#f3e9dc;font-size:15px;font-weight:700;line-height:1.6;word-break:keep-all;"><span aria-hidden="true">🔍</span> __PRISON_CLUE__</div>
      <div class="hud-panel rounded-xl p-3 mb-6 text-xs">
        <span class="text-amber-600 block uppercase font-bold">소요 시간</span>
        <span id="vicTime" class="text-base font-extrabold text-amber-200">00:00</span>
      </div>
      <button id="btnVictoryToJail" class="btn-gold w-full py-3 rounded-xl font-bold text-sm tracking-wide shadow-lg">감옥으로 돌아가기</button>
    </div>
  </div>
</div>

<!-- ============================================================ -->
<!-- 자물쇠 해제 연출                                                -->
<!-- ============================================================ -->
<div id="unlockModal" class="fixed inset-0 bg-black/80 backdrop-blur-md flex items-center justify-center p-4 z-50 hidden transition-opacity duration-300">
  <div class="wood-frame max-w-sm w-full p-6 text-center" id="unlockCard">
    <div class="unlock-scene">
      <svg class="lock-svg" id="lockSvg" viewBox="0 0 120 150" aria-hidden="true">
        <defs>
          <linearGradient id="lockBodyGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#6e4a1c"/>
            <stop offset=".5" stop-color="#4a2c1d"/>
            <stop offset="1" stop-color="#2b1810"/>
          </linearGradient>
          <linearGradient id="lockShackleGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="#e8c27a"/>
            <stop offset=".5" stop-color="#b6842f"/>
            <stop offset="1" stop-color="#6e4a1c"/>
          </linearGradient>
        </defs>
        <path class="lock-shackle" d="M40,66 V46 a20,20 0 0 1 40,0 V66" fill="none" stroke="url(#lockShackleGrad)" stroke-width="13" stroke-linecap="round"/>
        <rect class="lock-body" x="18" y="62" width="84" height="72" rx="12" fill="url(#lockBodyGrad)" stroke="#1a0e08" stroke-width="4"/>
        <circle cx="60" cy="94" r="10" fill="#1a0e08"/>
        <rect x="56" y="94" width="8" height="20" rx="2" fill="#1a0e08"/>
      </svg>
      <div class="unlock-key-wrap" id="unlockKeyWrap"></div>
      <div class="unlock-glow" id="unlockGlow"></div>
    </div>
    <div class="unlock-text" id="unlockText">
      <div class="unlock-title">🔓 잠금이 풀렸다!</div>
      <div class="unlock-sub">6번 열쇠가 자물쇠에 꼭 맞았다 — 감옥의 자물쇠가 열렸다</div>
    </div>
    <button id="btnUnlockClose" class="btn-gold w-full py-3 rounded-xl font-bold text-sm tracking-wide shadow-lg">계속하기</button>
  </div>
</div>

<div id="secretRoom" hidden>
  <div class="sr-title cinzel-font">Lucky!</div>
  <div class="sr-text">축하합니다. 당신은 1 / 20,000 (0.005%)을 뚫었습니다.<br>현재 부스에 있는 운영팀에게 이 사실을 알려주세요!</div>
  <input id="srPassword" class="sr-pass" type="password" placeholder="비밀번호" autocomplete="off" inputmode="numeric">
  <button id="btnSecretBack" class="btn-gold px-5 py-2 rounded-lg font-bold text-xs" disabled>🔒 돌아가기</button>
</div>

<div id="toast" role="status" aria-live="polite"></div>

<script>
(function(){
  "use strict";

  /* ========================================================
     오디오 합성 엔진
  ======================================================== */
  class AudioController{
    constructor(){ this.ctx=null; this.muted=false; }
    init(){
      if(!this.ctx){ const A=window.AudioContext||window.webkitAudioContext; if(A) this.ctx=new A(); }
      if(this.ctx && this.ctx.state==='suspended') this.ctx.resume();
    }
    playSlide(){
      if(this.muted||!this.ctx) return;
      try{
        const now=this.ctx.currentTime;
        const osc=this.ctx.createOscillator(), gain=this.ctx.createGain(), filter=this.ctx.createBiquadFilter();
        osc.type='triangle'; osc.frequency.setValueAtTime(120,now); osc.frequency.exponentialRampToValueAtTime(40, now+.12);
        filter.type='lowpass'; filter.frequency.setValueAtTime(300,now);
        gain.gain.setValueAtTime(.4,now); gain.gain.exponentialRampToValueAtTime(.01, now+.12);
        osc.connect(filter); filter.connect(gain); gain.connect(this.ctx.destination);
        osc.start(now); osc.stop(now+.12);
      }catch(e){}
    }
    playRotate(){
      if(this.muted||!this.ctx) return;
      try{
        const now=this.ctx.currentTime;
        const osc=this.ctx.createOscillator(), gain=this.ctx.createGain();
        osc.type='sine'; osc.frequency.setValueAtTime(400,now); osc.frequency.exponentialRampToValueAtTime(800, now+.08);
        gain.gain.setValueAtTime(.3,now); gain.gain.exponentialRampToValueAtTime(.01, now+.08);
        osc.connect(gain); gain.connect(this.ctx.destination);
        osc.start(now); osc.stop(now+.08);
      }catch(e){}
    }
    playBuzz(){
      if(this.muted||!this.ctx) return;
      try{
        const now=this.ctx.currentTime;
        const osc=this.ctx.createOscillator(), gain=this.ctx.createGain();
        osc.type='sawtooth'; osc.frequency.setValueAtTime(140,now); osc.frequency.linearRampToValueAtTime(90, now+.18);
        gain.gain.setValueAtTime(.22,now); gain.gain.exponentialRampToValueAtTime(.01, now+.2);
        osc.connect(gain); gain.connect(this.ctx.destination);
        osc.start(now); osc.stop(now+.2);
      }catch(e){}
    }
    playWaterFlow(){
      if(this.muted||!this.ctx) return;
      try{
        const now=this.ctx.currentTime;
        const bufferSize=this.ctx.sampleRate*1.5;
        const buffer=this.ctx.createBuffer(1,bufferSize,this.ctx.sampleRate);
        const data=buffer.getChannelData(0);
        for(let i=0;i<bufferSize;i++) data[i]=Math.random()*2-1;
        const noise=this.ctx.createBufferSource(); noise.buffer=buffer;
        const filter=this.ctx.createBiquadFilter(); filter.type='bandpass'; filter.frequency.setValueAtTime(600,now); filter.Q.setValueAtTime(3,now);
        const gain=this.ctx.createGain(); gain.gain.setValueAtTime(.01,now); gain.gain.linearRampToValueAtTime(.3, now+.3); gain.gain.exponentialRampToValueAtTime(.01, now+1.5);
        noise.connect(filter); filter.connect(gain); gain.connect(this.ctx.destination);
        noise.start(now); noise.stop(now+1.5);
      }catch(e){}
    }
    playFanfare(){
      if(this.muted||!this.ctx) return;
      try{
        const notes=[261.63,329.63,392.00,523.25,659.25,783.99];
        notes.forEach((freq,i)=>{
          const now=this.ctx.currentTime+i*.09;
          const osc=this.ctx.createOscillator(), gain=this.ctx.createGain();
          osc.type='triangle'; osc.frequency.setValueAtTime(freq,now);
          gain.gain.setValueAtTime(.3,now); gain.gain.exponentialRampToValueAtTime(.001, now+.4);
          osc.connect(gain); gain.connect(this.ctx.destination);
          osc.start(now); osc.stop(now+.4);
        });
      }catch(e){}
    }
    playKeyClank(){
      if(this.muted||!this.ctx) return;
      try{
        [0, 0.11].forEach((offset)=>{
          const t=this.ctx.currentTime+offset;
          const osc=this.ctx.createOscillator(), gain=this.ctx.createGain(), filter=this.ctx.createBiquadFilter();
          osc.type='square'; osc.frequency.setValueAtTime(520,t); osc.frequency.exponentialRampToValueAtTime(180, t+.09);
          filter.type='bandpass'; filter.frequency.setValueAtTime(900,t); filter.Q.setValueAtTime(4,t);
          gain.gain.setValueAtTime(.18,t); gain.gain.exponentialRampToValueAtTime(.001, t+.1);
          osc.connect(filter); filter.connect(gain); gain.connect(this.ctx.destination);
          osc.start(t); osc.stop(t+.1);
        });
      }catch(e){}
    }
    playUnlockClick(){
      if(this.muted||!this.ctx) return;
      try{
        const now=this.ctx.currentTime;
        const osc=this.ctx.createOscillator(), gain=this.ctx.createGain(), filter=this.ctx.createBiquadFilter();
        osc.type='triangle'; osc.frequency.setValueAtTime(300,now); osc.frequency.exponentialRampToValueAtTime(760, now+.07);
        filter.type='highpass'; filter.frequency.setValueAtTime(200,now);
        gain.gain.setValueAtTime(.35,now); gain.gain.exponentialRampToValueAtTime(.01, now+.16);
        osc.connect(filter); filter.connect(gain); gain.connect(this.ctx.destination);
        osc.start(now); osc.stop(now+.16);
      }catch(e){}
    }
  }
  const audio = new AudioController();

  /* ========================================================
     토스트
  ======================================================== */
  const toastEl = document.getElementById('toast');
  let toastTimer=null;
  function showToast(msg, type){
    toastEl.textContent = msg;
    toastEl.className = type || '';
    requestAnimationFrame(()=> toastEl.classList.add('show'));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(()=> toastEl.classList.remove('show'), 2200);
  }

  /* ========================================================
     감옥 화면: 열쇠 7개
  ======================================================== */
  function buildKeySVG(n){
    return `<svg class="key-svg" viewBox="0 0 60 150" aria-hidden="true">
      <defs>
        <linearGradient id="km${n}" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#e8c27a"/>
          <stop offset=".5" stop-color="#b6842f"/>
          <stop offset="1" stop-color="#6e4a1c"/>
        </linearGradient>
      </defs>
      <circle cx="30" cy="28" r="24" fill="url(#km${n})" stroke="#2b1810" stroke-width="4"/>
      <circle cx="30" cy="28" r="11" fill="#1a0e08"/>
      <text x="30" y="35" text-anchor="middle" font-family="Cinzel, serif" font-weight="700" font-size="18" fill="#2b1810">${n}</text>
      <rect x="24" y="50" width="12" height="66" fill="url(#km${n})" stroke="#2b1810" stroke-width="3"/>
      <rect x="36" y="96" width="16" height="9" fill="url(#km${n})" stroke="#2b1810" stroke-width="2"/>
      <rect x="36" y="112" width="11" height="9" fill="url(#km${n})" stroke="#2b1810" stroke-width="2"/>
    </svg>`;
  }

  let keysUnlocked = false;

  function buildKeyRack(){
    const rack = document.getElementById('keyRack');
    rack.innerHTML = '';
    for(let i=1;i<=7;i++){
      const btn = document.createElement('button');
      btn.className = 'key-btn';
      btn.setAttribute('aria-label', `${i}번 열쇠로 시도하기`);
      btn.innerHTML = `${buildKeySVG(i)}<span class="key-label">${i}번 열쇠</span><span class="key-lock-badge" aria-hidden="true">🔒</span>`;
      btn.addEventListener('click', ()=>{
        audio.init();
        if(!keysUnlocked){
          showToast('아직 열쇠를 시도할 수 없어요. 먼저 퍼즐을 풀어 보세요!', 'fail');
          audio.playBuzz();
          return;
        }
        if(i === 6){ playUnlockCelebration(); }
        else { showToast('이 열쇠는 아닌 것 같아요. 다른 열쇠를 시도해 보세요!', 'fail'); audio.playBuzz(); }
      });
      rack.appendChild(btn);
    }
    refreshKeyLockUI();
  }

  function refreshKeyLockUI(){
    const rack = document.getElementById('keyRack');
    if(!rack) return;
    rack.classList.toggle('locked', !keysUnlocked);
    const step1 = document.getElementById('questStep1');
    if(step1) step1.classList.toggle('done', keysUnlocked);
    rack.querySelectorAll('.key-btn').forEach(btn=> btn.setAttribute('aria-disabled', String(!keysUnlocked)));
    const sub = document.getElementById('prisonSub');
    if(sub){
      sub.textContent = keysUnlocked
        ? '일곱 개의 열쇠 중 자물쇠를 여는 열쇠는 단 하나뿐이에요'
        : '열쇠는 아직 잠겨 있어요. 먼저 퍼즐을 풀어 단서를 찾아 보세요!';
    }
  }

  function unlockKeys(){
    if(keysUnlocked) return;
    keysUnlocked = true;
    refreshKeyLockUI();
  }

  /* ========================================================
     6번 열쇠 성공 시: 자물쇠가 열리는 연출
  ======================================================== */
  let unlockAnimTimers = [];

  function playUnlockCelebration(){
    audio.init();

    const modal = document.getElementById('unlockModal');
    const card = document.getElementById('unlockCard');
    const lockSvg = document.getElementById('lockSvg');
    const keyWrap = document.getElementById('unlockKeyWrap');
    const glow = document.getElementById('unlockGlow');
    const text = document.getElementById('unlockText');

    // 상태 초기화 (재생 중 다시 열릴 경우 대비)
    unlockAnimTimers.forEach(t=> clearTimeout(t));
    unlockAnimTimers = [];
    lockSvg.classList.remove('shackle-open');
    glow.classList.remove('pulse');
    text.classList.remove('show');
    card.classList.remove('pop-in');
    keyWrap.classList.remove('key-turn');
    keyWrap.innerHTML = buildKeySVG(6);

    modal.classList.remove('hidden');
    void card.offsetWidth; // 강제 리플로우로 애니메이션 재시작 보장
    card.classList.add('pop-in');

    audio.playKeyClank();
    unlockAnimTimers.push(setTimeout(()=>{
      keyWrap.classList.add('key-turn');
      audio.playRotate();
    }, 300));
    unlockAnimTimers.push(setTimeout(()=>{
      lockSvg.classList.add('shackle-open');
      glow.classList.add('pulse');
      audio.playUnlockClick();
      if(window.confetti) confetti({ particleCount:80, spread:70, origin:{y:.5} });
    }, 780));
    unlockAnimTimers.push(setTimeout(()=>{
      text.classList.add('show');
      audio.playFanfare();
    }, 1050));
  }

  function closeUnlockModal(){
    unlockAnimTimers.forEach(t=> clearTimeout(t));
    unlockAnimTimers = [];
    document.getElementById('unlockModal').classList.add('hidden');
  }

  function buildPrisonBars(){
    const wrap = document.getElementById('prisonBars');
    const w = window.innerWidth || 800;
    const count = Math.max(6, Math.round(w/70));
    let html='';
    for(let i=0;i<count;i++){
      const left = (i/(count-1))*100;
      html += `<div class="bar" style="left:${left}%;"></div>`;
    }
    wrap.innerHTML = html;
  }

  /* ========================================================
     용의자 화면: 6번 열쇠로 자물쇠를 풀고 나면 등장하는 3인 지목 화면
  ======================================================== */
  // 목격자 진술(녹색 장갑 / 왼손으로 문을 열었다 / 파란색 계열 시계)을 근거로
  // 세 용의자의 생김새(장갑 색 · 문을 연 손 · 시계 색)를 서로 다르게 구성한다.
  // 세 단서를 모두 만족하는 사람이 스토커로 확정된다.
  const SUSPECT_DATA = [
    { id:'A', glove:'#2ecc71', gloveName:'녹색', hand:'both', handName:'양손', watch:'#2f7dff', watchName:'파란색 계열', watchShort:'파랑' },
    { id:'B', glove:'#2ecc71', gloveName:'녹색', hand:'left', handName:'왼손', watch:'#2f7dff', watchName:'파란색 계열', watchShort:'파랑' },
    { id:'C', glove:'#f3ece0', gloveName:'흰색', hand:'both', handName:'양손', watch:'#2f7dff', watchName:'파란색 계열', watchShort:'파랑' },
  ];
  // A는 장갑·시계 색은 B와 같지만 "양손"을 써서 단서②(왼손)와 어긋난다.
  // 녹색 장갑 + 왼손 + 파란색 계열 시계 = 세 목격담과 모두 일치하는 용의자 B가 스토커
  const STALKER_ID = 'B';

  function buildSuspectSVG(s){
    // 손은 항상 한 쌍이지만, 문을 열 때 실제로 쓴 손만 앞으로 내밀어 그려서
    // '몇 번째 손을 썼는지'가 한눈에 보이도록 한다.
    // (보는 사람 기준 왼쪽 = 목격담의 '왼손'으로 표기해 혼동 없이 읽히게 함)
    const showLeft = s.hand === 'left' || s.hand === 'both';
    const showRight = s.hand === 'both';
    let handsSvg = '';
    if(showLeft){
      handsSvg += `<rect x="13" y="100" width="10" height="11" rx="3" fill="${s.glove}" stroke="#0d0a06" stroke-width="1.4"/>
        <ellipse cx="18" cy="113" rx="9" ry="9.5" fill="${s.glove}" stroke="#0d0a06" stroke-width="1.4"/>`;
    }
    if(showRight){
      handsSvg += `<rect x="57" y="100" width="10" height="11" rx="3" fill="${s.glove}" stroke="#0d0a06" stroke-width="1.4"/>
        <ellipse cx="62" cy="113" rx="9" ry="9.5" fill="${s.glove}" stroke="#0d0a06" stroke-width="1.4"/>`;
    }
    const watchX = showRight ? 62 : 18; // 시계는 실제로 보이는 손목에 표시
    const watchSvg = `<rect x="${watchX-6}" y="95" width="12" height="7" rx="2" fill="${s.watch}" stroke="#0d0a06" stroke-width="1.2"/>
      <circle cx="${watchX}" cy="98.5" r="2.3" fill="#1a120c" stroke="${s.watch}" stroke-width="1"/>`;
    return `<svg class="suspect-svg" viewBox="0 0 80 150" aria-hidden="true">
      <ellipse cx="40" cy="142" rx="28" ry="7" fill="rgba(0,0,0,.35)"/>
      <path d="M40,50 C18,50 10,72 10,100 L10,138 L70,138 L70,100 C70,72 62,50 40,50 Z" fill="#241a10" stroke="#0d0a06" stroke-width="2"/>
      ${handsSvg}
      ${watchSvg}
      <ellipse cx="40" cy="32" rx="17" ry="19" fill="#1a120c" stroke="#0d0a06" stroke-width="2"/>
    </svg>`;
  }

  function buildSuspects(){
    const row = document.getElementById('suspectRow');
    if(!row) return;
    row.innerHTML = '';
    SUSPECT_DATA.forEach(s=>{
      const btn = document.createElement('button');
      btn.className = 'suspect-btn';
      btn.setAttribute('aria-label', `용의자 ${s.id}: 장갑 ${s.gloveName}, ${s.handName}으로 문을 열었음, 시계 ${s.watchName}. 지목하기`);
      // 그림은 보는 사람 기준으로 그려져 있어서(화면 왼쪽 = 왼손), 표시도 화면 기준 왼쪽/오른쪽이다.
      btn.innerHTML = `${buildSuspectSVG(s)}<span class="suspect-id">용의자 ${s.id}</span>`
        + `<span class="suspect-side" aria-hidden="true"><span>왼쪽</span><i></i><span>오른쪽</span></span>`;
      btn.addEventListener('click', ()=>{
        audio.init();
        if(s.id === STALKER_ID){
          audio.playRotate();
          // 방금 누른 오답 토스트가 아직 떠 있으면 새 화면 위에 남지 않도록 치운다.
          clearTimeout(toastTimer); toastEl.classList.remove('show');
          applyView('found'); // 스토커를 찾았다는 안내 화면으로 넘어간다
        }else{
          const penalized = addWrongSuspectPenalty(s.id);
          showToast(penalized ? '스토커가 아니에요. 다른 용의자를 골라 보세요 (+3분)' : '스토커가 아니에요. 다른 용의자를 골라 보세요', 'fail');
          audio.playBuzz();
        }
      });
      row.appendChild(btn);
    });
  }

  /* ========================================================
     화면 전환
  ======================================================== */
  // 이 iframe 의 높이를 바깥 페이지에서 보이는 높이에 맞춰서, 화면 아래에 빈 공간이 생기지 않게 한다.
  //  - 스토커를 찾은 화면·마지막 화면: '우측 하단' 버튼이 화면 아래쪽에 보이도록, 보이는 높이에 딱 맞춘다.
  //    (그대로 두면 820px 고정이라 낮은 폰 화면에서는 버튼이 화면 아래로 밀려 스크롤해야 보인다.)
  //  - 나머지 화면: 820px 보다 줄이지는 않고(감옥 화면은 스크롤해서 본다), 창이 더 크면 창 높이까지 키운다.
  //  iframe 만 바꾸면 안 되고, 그것을 감싼 Streamlit 칸의 높이도 같이 바꿔야 한다. (아니면 그 차이만큼 아래가 빈다.)
  function fitFrameToParentViewport(){
    try{
      const fr = window.frameElement, pw = window.parent;
      if(!fr || !pw) return;
      const shrink = (currentView === 'found' || currentView === 'finish');
      const main = pw.document.querySelector('[data-testid="stMain"]');
      let top;
      if(shrink){
        // 앞 화면에서 스크롤해 둔 상태라면 iframe 윗부분이 화면 밖에 있으므로, 바깥 페이지를 맨 위로 올린 뒤 잰다.
        if(main) main.scrollTop = 0;
        pw.scrollTo(0, 0);
        top = Math.max(0, fr.getBoundingClientRect().top);
      }else{
        // 스크롤 위치는 건드리지 않고, 페이지 맨 위에서 iframe 까지의 거리를 잰다.
        top = Math.max(0, fr.getBoundingClientRect().top + (main ? main.scrollTop : 0) + (pw.scrollY || 0));
      }
      // 아래쪽 안쪽 여백(페이지 맨 아래의 padding)까지 빼서, 스크롤 없이 딱 맞게 한다.
      const blockEl = pw.document.querySelector('[data-testid="stMainBlockContainer"]');
      const padBottom = blockEl ? (parseFloat(pw.getComputedStyle(blockEl).paddingBottom) || 0) : 0;
      const fill = Math.floor(pw.innerHeight - top - padBottom);
      const h = shrink ? Math.max(420, fill) : Math.max(900, fill);
      fr.style.height = h + 'px';
      // 칸은 flex 항목이라 flex-basis 가 820px 로 잡혀 있어서, height 와 함께 flex 도 바꿔야 바뀐다.
      const box = fr.closest('[data-testid="stElementContainer"]');
      if(box){ box.style.height = h + 'px'; box.style.minHeight = '0'; box.style.flex = '0 0 ' + h + 'px'; }
    }catch(e){ /* 접근이 막히면 기본 높이(820px) 그대로 둔다 */ }
  }
  window.addEventListener('resize', fitFrameToParentViewport);

  /* ========================================================
     스톱워치
     난이도를 선택한 때부터 마지막 "다음으로"를 누를 때까지 걸린 시간을 잰다.
     숫자는 앱 화면 우측 상단에 작게 보이고(파이썬 쪽 show_stopwatch), 여기서는 마지막 "다음으로"에서
     그 스톱워치를 멈춰 시간을 읽는다. (window.parent.__sw)
     앱 화면의 스톱워치를 쓸 수 없을 때를 위해, 이 화면을 만들 때까지 흐른 시간(ELAPSED_BEFORE_MS)에
     이 화면이 열린 뒤 흐른 시간을 더하는 계산도 같이 둔다.
     (서버 시계와 접속한 기기의 시계를 직접 비교하지 않고 '흐른 시간'만 더하므로 시계가 달라도 맞다.)
  ======================================================== */
  const ELAPSED_BEFORE_MS = Math.max(0, Number("__ELAPSED_BEFORE_MS__") || 0);
  const FRAME_OPENED_AT = Date.now();
  function elapsedMs(){ return ELAPSED_BEFORE_MS + (Date.now() - FRAME_OPENED_AT); }

  /* 오답 용의자 벌칙: 스토커(용의자 B)가 아닌 용의자를 지목하면 스톱워치에 3분이 더해진다.
     용의자 한 명당 한 번만 더한다. (같은 사람을 또 눌러도 다시 더하지 않는다. 최대 A, C 두 명 = 6분)
     시간을 바꾸려면 아래 WRONG_SUSPECT_PENALTY_MS 만 고친다. */
  const WRONG_SUSPECT_PENALTY_MS = 3 * 60 * 1000;
  const penalizedSuspects = new Set();
  // 앱 화면의 스톱워치에 더하지 못했을 때(스톱워치를 쓸 수 없을 때) 이 화면이 대신 기억해 두었다가 마지막에 더한다.
  let localPenaltyMs = 0;
  function addWrongSuspectPenalty(suspectId){
    if(penalizedSuspects.has(suspectId)) return false;
    penalizedSuspects.add(suspectId);
    try{
      const w = window.parent;
      if(w && w.__sw && typeof w.__sw.penalize === 'function'){ w.__sw.penalize(WRONG_SUSPECT_PENALTY_MS); return true; }
    }catch(e){ /* 접근이 막히면 아래의 자체 계산을 쓴다 */ }
    localPenaltyMs += WRONG_SUSPECT_PENALTY_MS;
    return true;
  }

  function stopParentStopwatch(){
    try{
      const w = window.parent;
      if(w && w.__sw && typeof w.__sw.stop === 'function') return w.__sw.stop();
    }catch(e){ /* 접근이 막히면 아래의 자체 계산을 쓴다 */ }
    return null;
  }
  // 우측 상단 스톱워치(분:초.밀리초)의 분·초와 같게 보이도록, 초 아래는 버린다.
  function formatElapsed(ms){
    const total = Math.floor(ms / 1000);
    return `${Math.floor(total / 60)}분 ${total % 60}초`;
  }

  let currentView = 'prison';
  function applyView(name, opts){
    opts = opts || {};
    currentView = name;
    document.getElementById('prisonView').hidden = name !== 'prison';
    document.getElementById('suspectsView').hidden = name !== 'suspects';
    document.getElementById('foundView').hidden = name !== 'found';
    document.getElementById('finishView').hidden = name !== 'finish';
    document.getElementById('gameView').hidden = name !== 'game';
    fitFrameToParentViewport(); // 안내문을 숨기거나 보인 뒤(위치가 바뀐 뒤)에 맞춘다
    if(name === 'finish'){
      // 클리어 화면이 뜨면 브라우저 탭(헤더) 제목을 "클리어"로 바꾼다.
      try{ window.parent.document.title = '클리어'; }catch(e){ /* 접근이 막히면 제목은 그대로 둔다 */ }
    }
    if(name === 'game' && !opts.skipRegen){
      GAME_LEVELS[0] = generateLevelSafe(1, 8);
      loadLevel();
    }
  }

  /* ========================================================
     파이프 퍼즐 게임 (원본 로직)
  ======================================================== */
  let canvas, ctx;
  let boardGrid = [];
  let boardSize = 4;
  let totalMoves = 0;
  let interactionMode = 'slide';
  let moveHistory = [];
  let timerSeconds = 0;
  let timerInterval = null;
  let isLevelSolved = false;
  let connectedPathIndices = new Set();

  let activeAnimations = [];
  let animFrameId = null;

  function easeOutCubic(t){ return 1 - Math.pow(1-t, 3); }
  function easeOutBack(t){ const c1=1.3, c3=c1+1; return 1 + c3*Math.pow(t-1,3) + c1*Math.pow(t-1,2); }

  // scale: 화면 배율(devicePixelRatio)에 맞춰 무늬를 더 촘촘한 해상도로 만든다.
  // (그냥 256px 로 만들면 고해상도 화면에서 늘려 그려져서 흐릿해진다.)
  function createWoodPatternCanvas(size, scale){
    const c = document.createElement('canvas'); c.width=size*scale; c.height=size*scale;
    const cx = c.getContext('2d');
    cx.scale(scale, scale);
    const grad = cx.createLinearGradient(0,0,size,size);
    grad.addColorStop(0,'#5c3826'); grad.addColorStop(.5,'#4a2c1d'); grad.addColorStop(1,'#3a2114');
    cx.fillStyle = grad; cx.fillRect(0,0,size,size);
    cx.strokeStyle='rgba(20,10,5,0.15)'; cx.lineWidth=2;
    for(let i=-size;i<size*2;i+=6){
      cx.beginPath(); cx.moveTo(i,0);
      cx.bezierCurveTo(i+20, size*.3, i-15, size*.7, i+10, size);
      cx.stroke();
    }
    for(let j=0;j<300;j++){
      cx.fillStyle = Math.random()>.5 ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.05)';
      cx.fillRect(Math.random()*size, Math.random()*size, 2, 2);
    }
    return c;
  }
  let woodPatternScale = 1;
  let woodPatternCanvas = createWoodPatternCanvas(256, woodPatternScale);
  function syncWoodPatternScale(dpr){
    const scale = Math.max(1, Math.ceil(dpr));
    if(scale === woodPatternScale) return;
    woodPatternScale = scale;
    woodPatternCanvas = createWoodPatternCanvas(256, scale);
  }

  const PIPE_DEFINITIONS = {
    'STRAIGHT': { ports:[true,false,true,false], name:'직선 파이프' },
    'ELBOW':    { ports:[true,true,false,false], name:'곡선 엘보' },
    'T_JOIN':   { ports:[true,true,false,true],  name:'T자 분기' },
    'CROSS':    { ports:[true,true,true,true],   name:'십자 교차' },
    'START':    { ports:[true,false,true,false], name:'시작 입구' },
    'END':      { ports:[true,false,true,false], name:'최종 출구' }
  };

  function getRotatedPorts(basePorts, rotationDeg){
    const shift = (Math.round(rotationDeg/90)%4 + 4) % 4;
    const ports = [false,false,false,false];
    for(let i=0;i<4;i++) ports[(i+shift)%4] = basePorts[i];
    return ports;
  }

  function buildCandidateGrid(GRID_SIZE, totalCells){
    let path = [];
    let visited = new Set();

    function findPath(r,c,currentPath){
      const idx = r*GRID_SIZE+c;
      currentPath.push({r,c,idx}); visited.add(idx);
      if(r===GRID_SIZE-1 && c===GRID_SIZE-1){ path=[...currentPath]; return true; }
      const dirs=[{dr:0,dc:1},{dr:1,dc:0},{dr:-1,dc:0},{dr:0,dc:-1}];
      dirs.sort(()=>Math.random()-.5);
      for(const d of dirs){
        const nr=r+d.dr, nc=c+d.dc, nidx=nr*GRID_SIZE+nc;
        if(nr>=0 && nr<GRID_SIZE && nc>=0 && nc<GRID_SIZE && !visited.has(nidx)){
          if(findPath(nr,nc,currentPath)) return true;
        }
      }
      currentPath.pop(); visited.delete(idx); return false;
    }
    findPath(0,0,[]);

    const grid = new Array(totalCells).fill(null);

    const startOutDir = getDirection(0,0, path[1].r, path[1].c);
    const startPipe = getFittingPipeType(0, startOutDir);
    grid[0] = { type:startPipe.type, rot:startPipe.rot, movable:false, rotatable:false, isStart:true };

    const prevPathNode = path[path.length-2];
    const endInDir = getDirection(3,3, prevPathNode.r, prevPathNode.c);
    const endPipe = getFittingPipeType(endInDir, 2);
    grid[totalCells-1] = { type:endPipe.type, rot:endPipe.rot, movable:false, rotatable:false, isEnd:true };

    for(let i=1;i<path.length-1;i++){
      const curr=path[i], prev=path[i-1], next=path[i+1];
      const inDir=getDirection(curr.r,curr.c,prev.r,prev.c);
      const outDir=getDirection(curr.r,curr.c,next.r,next.c);
      const pipeConfig=getFittingPipeType(inDir,outDir);
      grid[curr.idx] = { type:pipeConfig.type, rot:pipeConfig.rot, movable:true, rotatable:true };
    }

    const fillerPipeTypes=['ELBOW','STRAIGHT','T_JOIN','BLOCK'];
    let emptyAssigned=false;
    for(let i=0;i<totalCells;i++){
      if(grid[i]!==null) continue;
      if(!emptyAssigned){ grid[i]={type:'EMPTY',rot:0,movable:true,rotatable:false}; emptyAssigned=true; continue; }
      const randomType = fillerPipeTypes[Math.floor(Math.random()*fillerPipeTypes.length)];
      const randomRot = [0,90,180,270][Math.floor(Math.random()*4)];
      grid[i] = { type:randomType, rot:randomRot, movable:randomType!=='BLOCK', rotatable:randomType!=='BLOCK' && randomType!=='EMPTY' };
    }
    if(!emptyAssigned){
      for(let i=1;i<totalCells-1;i++){
        if(!grid[i].isStart && !grid[i].isEnd && grid[i].type!=='START' && grid[i].type!=='END'){
          grid[i] = {type:'EMPTY',rot:0,movable:true,rotatable:false}; break;
        }
      }
    }
    return grid;
  }

  function generateSolvable4x4Level(levelId, minMoves){
    levelId = levelId || 1; minMoves = minMoves || 8;
    const GRID_SIZE = 4;
    const totalCells = GRID_SIZE*GRID_SIZE;

    let grid = null;
    let rebuildAttempts = 0;
    do{
      grid = buildCandidateGrid(GRID_SIZE, totalCells);
      let shuffleAttempts = 0;
      let connected = true;
      do{
        scrambleGrid(grid, GRID_SIZE, 30 + Math.floor(Math.random()*20));
        connected = solvePipePath(grid, GRID_SIZE).isConnected;
        shuffleAttempts++;
      } while(connected && shuffleAttempts < 15);
      rebuildAttempts++;
      if(!connected) break;
    } while(rebuildAttempts < 40);

    return { id:levelId, title:`4x4 퍼즐 - 스테이지 ${levelId}`, size:GRID_SIZE, minMoves, grid };
  }

  function getDirection(fromR,fromC,toR,toC){
    if(toR<fromR) return 0; if(toC>fromC) return 1; if(toR>fromR) return 2; if(toC<fromC) return 3; return 0;
  }

  function getFittingPipeType(inDir,outDir){
    const diff = Math.abs(inDir-outDir);
    if(diff===2){
      const rot = (inDir===0||outDir===0) ? 0 : 90;
      return { type:'STRAIGHT', rot };
    }
    const portsNeeded=[false,false,false,false];
    portsNeeded[inDir]=true; portsNeeded[outDir]=true;
    for(let r=0;r<360;r+=90){
      const testPorts = getRotatedPorts(PIPE_DEFINITIONS['ELBOW'].ports, r);
      if(testPorts[0]===portsNeeded[0] && testPorts[1]===portsNeeded[1] && testPorts[2]===portsNeeded[2] && testPorts[3]===portsNeeded[3]){
        return { type:'ELBOW', rot:r };
      }
    }
    return { type:'ELBOW', rot:0 };
  }

  function scrambleGrid(grid, size, shuffleSteps){
    let emptyIdx = grid.findIndex(t=>t.type==='EMPTY');
    let lastMovedIdx = -1;
    for(let step=0; step<shuffleSteps; step++){
      const emptyRow=Math.floor(emptyIdx/size), emptyCol=emptyIdx%size;
      const neighbors=[];
      for(let d=0;d<4;d++){
        let nr=emptyRow, nc=emptyCol;
        if(d===0) nr--; if(d===1) nc++; if(d===2) nr++; if(d===3) nc--;
        if(nr>=0 && nr<size && nc>=0 && nc<size){
          const nidx=nr*size+nc;
          if(grid[nidx].movable && nidx!==lastMovedIdx) neighbors.push(nidx);
        }
      }
      if(neighbors.length>0){
        const choice = neighbors[Math.floor(Math.random()*neighbors.length)];
        grid[emptyIdx] = { ...grid[choice] };
        grid[choice] = { type:'EMPTY', rot:0, movable:true, rotatable:false };
        lastMovedIdx = emptyIdx; emptyIdx = choice;
      }
    }
  }

  let GAME_LEVELS = [ null ];

  function solvePipePath(grid, size){
    let startIdx=-1, endIdx=-1;
    for(let i=0;i<grid.length;i++){
      if(grid[i].isStart || grid[i].type==='START') startIdx=i;
      if(grid[i].isEnd || grid[i].type==='END') endIdx=i;
    }
    if(startIdx===-1 || endIdx===-1) return { isConnected:false, path:[] };

    const oppositeDir=[2,3,0,1];
    const queue=[[startIdx]];
    const visited=new Set([startIdx]);

    while(queue.length>0){
      const currentPath = queue.shift();
      const currIdx = currentPath[currentPath.length-1];
      if(currIdx===endIdx) return { isConnected:true, path:currentPath };

      const currTile = grid[currIdx];
      if(!PIPE_DEFINITIONS[currTile.type]) continue;

      const currPorts = getRotatedPorts(PIPE_DEFINITIONS[currTile.type].ports, currTile.rot);
      const currRow = Math.floor(currIdx/size), currCol = currIdx%size;

      for(let d=0;d<4;d++){
        if(!currPorts[d]) continue;
        let nr=currRow, nc=currCol;
        if(d===0) nr--; if(d===1) nc++; if(d===2) nr++; if(d===3) nc--;
        if(nr>=0 && nr<size && nc>=0 && nc<size){
          const nidx=nr*size+nc;
          if(visited.has(nidx)) continue;
          const neighborTile = grid[nidx];
          if(!neighborTile || neighborTile.type==='EMPTY' || neighborTile.type==='BLOCK') continue;
          const neighborPorts = getRotatedPorts(PIPE_DEFINITIONS[neighborTile.type].ports, neighborTile.rot);
          const oppositeD = oppositeDir[d];
          if(neighborPorts[oppositeD]){ visited.add(nidx); queue.push([...currentPath, nidx]); }
        }
      }
    }
    return { isConnected:false, path:[] };
  }

  /* 스크램블 직후 우연히 이미 연결(클리어)된 상태로 나오지 않도록 보장 */
  function generateLevelSafe(levelId, minMoves){
    let level;
    for(let attempt=0; attempt<60; attempt++){
      level = generateSolvable4x4Level(levelId, minMoves);
      if(!solvePipePath(level.grid, level.size).isConnected) return level;
    }
    for(let i=0;i<level.grid.length;i++){
      const t = level.grid[i];
      if(!t.rotatable || t.isStart || t.isEnd || t.type==='EMPTY' || t.type==='BLOCK') continue;
      const originalRot = t.rot;
      for(let r=1;r<=3;r++){
        t.rot = (originalRot + 90*r) % 360;
        if(!solvePipePath(level.grid, level.size).isConnected) return level;
      }
      t.rot = originalRot;
    }
    return level;
  }

  function startAnimationLoop(onComplete){
    if(animFrameId) cancelAnimationFrame(animFrameId);
    function step(){
      const now = performance.now();
      render();
      activeAnimations = activeAnimations.filter(a => (now-a.startTime) < a.duration);
      if(activeAnimations.length>0){ animFrameId = requestAnimationFrame(step); }
      else{ animFrameId=null; render(); if(onComplete) onComplete(); }
    }
    animFrameId = requestAnimationFrame(step);
  }

  function render(){
    if(!canvas || !ctx) return;
    const renderSize = getBoardMetrics().size;
    ctx.clearRect(0,0,renderSize,renderSize);
    const cellSize = renderSize/boardSize;

    const animatingIndices = new Set();
    for(const anim of activeAnimations){
      if(anim.type==='slide') animatingIndices.add(anim.destIdx);
      else if(anim.type==='rotate') animatingIndices.add(anim.tileIdx);
    }

    for(let i=0;i<boardGrid.length;i++){
      const row=Math.floor(i/boardSize), col=i%boardSize;
      const x=col*cellSize, y=row*cellSize;
      if(animatingIndices.has(i)){
        drawTile(x,y,cellSize, {type:'EMPTY',rot:0,movable:true,rotatable:false}, false);
      }else{
        const tile = boardGrid[i];
        const isPath = connectedPathIndices.has(i);
        drawTile(x,y,cellSize, tile, isPath);
      }
    }

    const now = performance.now();
    for(const anim of activeAnimations){
      const elapsed = now - anim.startTime;
      const rawProgress = Math.min(1, elapsed/anim.duration);
      if(anim.type==='slide'){
        const ease = easeOutCubic(rawProgress);
        const currentX=(anim.srcCol + (anim.destCol-anim.srcCol)*ease)*cellSize;
        const currentY=(anim.srcRow + (anim.destRow-anim.srcRow)*ease)*cellSize;
        const isPath = connectedPathIndices.has(anim.destIdx);
        ctx.save(); ctx.shadowColor='rgba(0,0,0,0.45)'; ctx.shadowBlur=12; ctx.shadowOffsetY=4;
        drawTile(currentX,currentY,cellSize, anim.tile, isPath);
        ctx.restore();
      }else if(anim.type==='rotate'){
        const ease = easeOutBack(rawProgress);
        const currentRot = anim.startRot + 90*ease;
        const row=Math.floor(anim.tileIdx/boardSize), col=anim.tileIdx%boardSize;
        const x=col*cellSize, y=row*cellSize;
        const isPath = connectedPathIndices.has(anim.tileIdx);
        const animatedTile = { ...boardGrid[anim.tileIdx], rot:currentRot };
        ctx.save(); drawTile(x,y,cellSize, animatedTile, isPath); ctx.restore();
      }
    }
  }

  function drawTile(x,y,size,tile,isPath){
    if(tile.type==='EMPTY'){
      ctx.fillStyle='#140c08'; ctx.fillRect(x+2,y+2,size-4,size-4);
      ctx.strokeStyle='#2b170c'; ctx.lineWidth=1; ctx.strokeRect(x+3,y+3,size-6,size-6);
      return;
    }
    if(tile.type==='BLOCK'){
      ctx.fillStyle='#2a160d'; ctx.fillRect(x+2,y+2,size-4,size-4);
      ctx.strokeStyle='#5a341f'; ctx.lineWidth=3; ctx.strokeRect(x+4,y+4,size-8,size-8);
      ctx.strokeStyle='#3e2013'; ctx.lineWidth=2;
      ctx.beginPath();
      ctx.moveTo(x+10,y+10); ctx.lineTo(x+size-10,y+size-10);
      ctx.moveTo(x+size-10,y+10); ctx.lineTo(x+10,y+size-10);
      ctx.stroke();
      return;
    }

    ctx.save(); ctx.translate(x,y);
    const isStartOrEnd = tile.isStart || tile.isEnd || tile.type==='START' || tile.type==='END';
    if(isStartOrEnd){
      const metalGrad = ctx.createLinearGradient(0,0,size,size);
      metalGrad.addColorStop(0,'#495057'); metalGrad.addColorStop(.25,'#6c757d'); metalGrad.addColorStop(.5,'#adb5bd'); metalGrad.addColorStop(.75,'#6c757d'); metalGrad.addColorStop(1,'#343a40');
      ctx.fillStyle = metalGrad; ctx.fillRect(2,2,size-4,size-4);
      ctx.lineWidth=2; ctx.strokeStyle='#ced4da'; ctx.strokeRect(3,3,size-6,size-6);
      ctx.lineWidth=1; ctx.strokeStyle='#212529'; ctx.strokeRect(5,5,size-10,size-10);
      const rOffset=Math.max(6,size*.12), rRadius=Math.max(2,size*.035);
      ctx.fillStyle='#212529';
      [[rOffset,rOffset],[size-rOffset,rOffset],[rOffset,size-rOffset],[size-rOffset,size-rOffset]].forEach(([rx,ry])=>{
        ctx.beginPath(); ctx.arc(rx,ry,rRadius,0,Math.PI*2); ctx.fill();
      });
    }else{
      const woodPattern = ctx.createPattern(woodPatternCanvas,'repeat');
      // 고해상도로 만든 무늬를 원래 크기(256 CSS px)로 되돌려 깐다.
      if(woodPattern.setTransform && woodPatternScale>1){
        woodPattern.setTransform(new DOMMatrix().scale(1/woodPatternScale));
      }
      ctx.fillStyle = woodPattern; ctx.fillRect(2,2,size-4,size-4);
      ctx.lineWidth=2; ctx.strokeStyle='#8c5a3c'; ctx.strokeRect(3,3,size-6,size-6);
    }

    const half=size/2, pipeRadius=size*.18;
    ctx.save(); ctx.translate(half,half); ctx.rotate(tile.rot*Math.PI/180);

    const outerColor = isPath ? '#27ae60' : '#555';
    const innerColor = isPath ? '#2ecc71' : '#222';

    const drawPipePath = (color,width)=>{
      ctx.strokeStyle=color; ctx.lineWidth=width; ctx.lineCap='butt'; ctx.lineJoin='round';
      ctx.beginPath();
      if(tile.type==='STRAIGHT' || tile.type==='START' || tile.type==='END'){
        ctx.moveTo(0,-half); ctx.lineTo(0,half);
      }else if(tile.type==='ELBOW'){
        ctx.arc(half,-half,half, Math.PI/2, Math.PI, false);
      }else if(tile.type==='T_JOIN'){
        ctx.moveTo(-half,0); ctx.lineTo(half,0); ctx.moveTo(0,0); ctx.lineTo(0,-half);
      }else if(tile.type==='CROSS'){
        ctx.moveTo(-half,0); ctx.lineTo(half,0); ctx.moveTo(0,-half); ctx.lineTo(0,half);
      }
      ctx.stroke();
    };
    drawPipePath(outerColor, pipeRadius*1.8);
    drawPipePath(innerColor, pipeRadius*1.0);

    ctx.restore(); ctx.restore();
  }

  // 보드 크기 계산: 칸 하나가 기기 픽셀 '정수 개'가 되도록 맞춘다.
  // (칸 경계가 픽셀 사이에 걸치면 선이 번지고, 캔버스 해상도가 소수면 소수점 아래가 잘려 흐려진다.)
  function getBoardMetrics(){
    const dpr = window.devicePixelRatio || 1;
    const avail = Math.max(0, Math.min(canvas.parentElement.clientWidth - 24, 480));
    const cellDev = Math.max(1, Math.floor(avail*dpr/boardSize));
    return { dpr, cellDev, size: cellDev*boardSize/dpr };
  }

  function resizeCanvas(){
    if(!canvas) return;
    const m = getBoardMetrics();
    canvas.width = m.cellDev*boardSize; canvas.height = m.cellDev*boardSize;
    canvas.style.width = m.size+'px'; canvas.style.height = m.size+'px';
    ctx.setTransform(1,0,0,1,0,0); ctx.scale(m.dpr,m.dpr);
    // canvas.width 를 바꾸면 그리기 설정이 초기화되므로, 매번 다시 켠다.
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    syncWoodPatternScale(m.dpr);
    render();
  }

  function setInteractionMode(mode){
    interactionMode = mode;
    document.getElementById('modeSlide').classList.toggle('active', mode==='slide');
    document.getElementById('modeRotate').classList.toggle('active', mode==='rotate');
  }

  function onCanvasClick(e){
    if(isLevelSolved || activeAnimations.length>0) return;
    audio.init();
    const rect = canvas.getBoundingClientRect();
    const clickX = e.clientX-rect.left, clickY = e.clientY-rect.top;
    const renderSize = rect.width, cellSize = renderSize/boardSize;
    const col = Math.floor(clickX/cellSize), row = Math.floor(clickY/cellSize);
    const idx = row*boardSize+col;
    if(row<0||row>=boardSize||col<0||col>=boardSize) return;

    const clickedTile = boardGrid[idx];

    if(interactionMode==='slide'){
      if(!clickedTile.movable) return;
      const emptyIdx = boardGrid.findIndex(t=>t.type==='EMPTY');
      const emptyRow = Math.floor(emptyIdx/boardSize), emptyCol = emptyIdx%boardSize;
      const isAdjacent = Math.abs(row-emptyRow)+Math.abs(col-emptyCol)===1;
      if(isAdjacent){
        saveMoveHistory();
        boardGrid[emptyIdx] = { ...clickedTile };
        boardGrid[idx] = { type:'EMPTY', rot:0, movable:true, rotatable:false };
        totalMoves++; audio.playSlide();
        activeAnimations.push({ type:'slide', tile:{...clickedTile}, destIdx:emptyIdx, srcRow:row, srcCol:col, destRow:emptyRow, destCol:emptyCol, startTime:performance.now(), duration:180 });
        updateGameState();
      }
    }else if(interactionMode==='rotate'){
      if(!clickedTile.rotatable) return;
      saveMoveHistory();
      const prevRot = clickedTile.rot;
      clickedTile.rot = (clickedTile.rot+90)%360;
      totalMoves++; audio.playRotate();
      activeAnimations.push({ type:'rotate', tileIdx:idx, startRot:prevRot, startTime:performance.now(), duration:200 });
      updateGameState();
    }
  }

  function saveMoveHistory(){ moveHistory.push(JSON.parse(JSON.stringify(boardGrid))); }

  function undoMove(){
    if(moveHistory.length===0 || isLevelSolved || activeAnimations.length>0) return;
    boardGrid = moveHistory.pop();
    totalMoves = Math.max(0, totalMoves-1);
    updateGameState(false);
  }

  function updateGameState(checkWin){
    if(checkWin===undefined) checkWin=true;
    document.getElementById('txtMoves').textContent = totalMoves;
    const solveResult = solvePipePath(boardGrid, boardSize);
    connectedPathIndices = new Set(solveResult.path);
    const txtStatus = document.getElementById('txtPipeStatus');
    if(solveResult.isConnected){
      txtStatus.textContent = '🟢 연결됨'; txtStatus.className = 'text-xs sm:text-sm font-extrabold text-emerald-400';
    }else{
      txtStatus.textContent = '🔴 끊어짐'; txtStatus.className = 'text-xs sm:text-sm font-extrabold text-red-400';
    }
    if(activeAnimations.length>0){
      startAnimationLoop(()=>{ if(checkWin && solveResult.isConnected && !isLevelSolved) handleVictory(); });
    }else{
      render();
      if(checkWin && solveResult.isConnected && !isLevelSolved) handleVictory();
    }
  }

  function handleVictory(){
    isLevelSolved = true; stopTimer();
    audio.playWaterFlow(); audio.playFanfare();
    const banner = document.getElementById('waterStatusBanner');
    banner.classList.remove('opacity-0','-translate-y-4'); banner.classList.add('opacity-100','translate-y-0');
    if(window.confetti) confetti({ particleCount:80, spread:70, origin:{y:.6} });
    setTimeout(()=>{
      document.getElementById('vicTime').textContent = document.getElementById('txtTimer').textContent;
      document.getElementById('modalVictory').classList.remove('hidden');
    }, 1000);
  }

  function startTimer(){ stopTimer(); timerSeconds=0; updateTimerDisplay(); timerInterval=setInterval(()=>{ timerSeconds++; updateTimerDisplay(); },1000); }
  function stopTimer(){ if(timerInterval) clearInterval(timerInterval); }
  function updateTimerDisplay(){
    const mins=String(Math.floor(timerSeconds/60)).padStart(2,'0');
    const secs=String(timerSeconds%60).padStart(2,'0');
    document.getElementById('txtTimer').textContent = `${mins}:${secs}`;
  }

  function loadLevel(){
    if(animFrameId){ cancelAnimationFrame(animFrameId); animFrameId=null; }
    activeAnimations = [];
    const levelData = GAME_LEVELS[0];
    boardSize = levelData.size;
    boardGrid = JSON.parse(JSON.stringify(levelData.grid));
    totalMoves = 0; moveHistory = []; isLevelSolved = false;
    document.getElementById('txtLevelNum').textContent = '4x4 - 스테이지 1';
    const banner = document.getElementById('waterStatusBanner');
    banner.classList.add('opacity-0','-translate-y-4'); banner.classList.remove('opacity-100','translate-y-0');
    document.getElementById('modalVictory').classList.add('hidden');
    startTimer();
    updateGameState(false);
    resizeCanvas();
  }

  /* ========================================================
     초기화 및 이벤트 연결
  ======================================================== */
  /* ========================================================
     비밀의 방: 이 화면의 어떤 버튼이든 누를 때마다 2만분의 1 확률로
     원래 동작 대신 비밀의 방이 열린다. (1 로 바꾸면 항상 열린다)
  ======================================================== */
  const SECRET_ROOM_ODDS = 20000;
  function rollSecretRoom(){
    const buf = new Uint32Array(1);
    const limit = Math.floor(4294967296 / SECRET_ROOM_ODDS) * SECRET_ROOM_ODDS;
    let x;
    do{ window.crypto.getRandomValues(buf); x = buf[0]; }while(x >= limit);
    return (x % SECRET_ROOM_ODDS) === 0;
  }
  function installSecretRoom(){
    const room = document.getElementById('secretRoom');
    // 캡처 단계에서 가로채므로, 당첨되면 그 버튼에 걸린 원래 클릭 동작은 실행되지 않는다.
    document.addEventListener('click', function(e){
      const t = e.target && e.target.closest ? e.target.closest('button, [role="button"], .mode-option') : null;
      if(!t || t.closest('#secretRoom')) return;
      if(rollSecretRoom()){
        e.preventDefault(); e.stopPropagation();
        room.hidden = false;
      }
    }, true);
    // 돌아가기 버튼은 비밀번호를 맞게 넣어야 눌린다. (운영팀만 풀 수 있게 잠가 둔다.)
    // 비밀번호는 소스에 그대로 적지 않고 해시값(FNV-1a 32비트, 'lucky:' + 비밀번호)만 둔다.
    // (소스를 훑어봐도 바로 보이지 않게 하는 정도의 장치이다. 비밀번호를 바꾸면 이 값도 다시 계산해서 넣는다.)
    const SECRET_ROOM_PASSWORD_HASH = 0x8230e4d4;
    function secretHash(s){
      let h = 0x811c9dc5;
      for(let i = 0; i < s.length; i++){ h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
      return h >>> 0;
    }
    const pass = document.getElementById('srPassword');
    const back = document.getElementById('btnSecretBack');
    function syncSecretLock(){
      const ok = secretHash('lucky:' + pass.value) === SECRET_ROOM_PASSWORD_HASH;
      back.disabled = !ok;
      back.textContent = ok ? '돌아가기' : '🔒 돌아가기';
    }
    pass.addEventListener('input', syncSecretLock);
    back.addEventListener('click', ()=>{ room.hidden = true; pass.value = ''; syncSecretLock(); });
  }

  function boot(){
    installSecretRoom();
    canvas = document.getElementById('gameCanvas');
    ctx = canvas.getContext('2d');

    window.addEventListener('resize', ()=>{ buildPrisonBars(); resizeCanvas(); });

    canvas.addEventListener('click', onCanvasClick);
    document.getElementById('modeSlide').addEventListener('click', ()=>setInteractionMode('slide'));
    document.getElementById('modeRotate').addEventListener('click', ()=>setInteractionMode('rotate'));
    document.getElementById('btnUndo').addEventListener('click', undoMove);
    document.getElementById('btnReset').addEventListener('click', ()=> loadLevel());
    document.getElementById('btnNewPuzzle').addEventListener('click', ()=>{
      GAME_LEVELS[0] = generateLevelSafe(1, 8);
      loadLevel();
    });
    document.getElementById('btnReturnToJail').addEventListener('click', ()=>{
      if(isLevelSolved) unlockKeys(); // 클리어하고 돌아갈 때만 잠금 해제
      applyView('prison');
    });
    document.getElementById('btnVictoryToJail').addEventListener('click', ()=>{
      unlockKeys(); // 승리 모달에서 나가는 길은 항상 클리어된 상태
      applyView('prison');
    });
    document.getElementById('btnGoGame').addEventListener('click', ()=> applyView('game'));
    document.getElementById('btnFoundNext').addEventListener('click', ()=>{
      // 마지막 "다음으로": 우측 상단 스톱워치를 멈추고, 난이도를 고른 때부터 지금까지 걸린 시간을 알려 주고 끝낸다.
      const stopped = stopParentStopwatch();
      // 앱 화면의 스톱워치에 더한 벌칙은 이미 들어 있다. localPenaltyMs 는 거기에 더하지 못한 벌칙만 담고 있다.
      const ms = (stopped !== null ? stopped : elapsedMs()) + localPenaltyMs;
      document.getElementById('finishTime').textContent = `축하합니다. 당신은 ${formatElapsed(ms)} 걸렸습니다.`;
      applyView('finish');
    });
    document.getElementById('btnUnlockClose').addEventListener('click', ()=>{
      closeUnlockModal();
      applyView('suspects'); // 6번 열쇠로 잠금을 풀고 "계속하기"를 누르면 용의자 화면으로 이동
    });

    keysUnlocked = false; // 새로 들어올 때마다 항상 잠긴 상태로 시작
    buildKeyRack();
    buildPrisonBars();
    buildSuspects();

    applyView('prison');
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
</script>
</body>
</html>
"""


def get_timemachine_animation_html(
    start_value=-145_000_000,
    end_value=1592,
    duration_ms=TIMEMACHINE_ANIM_DURATION_MS,
    new_bg_url="https://i.postimg.cc/J04WRcYy/Agent-Image-A-lively-traditional-Korean-market-street-in-1592-during-the-Joseon-dynasty-lined-wi.png",
):
    return f"""
    <div id="tm-overlay" style="
        position:fixed; top:0; left:0; width:100vw; height:100vh; height:100dvh;
        overflow:hidden; background:transparent; margin:0; z-index:999999;
    ">
      <div id="tm-bg-fade" style="
          position:absolute; inset:0;
          background-image:url('{new_bg_url}');
          background-size:cover;
          background-position:center;
          background-repeat:no-repeat;
          opacity:0;
      "></div>

      <div id="tm-card" style="
          position:absolute; top:50%; left:50%; transform:translate(-50%, -50%);
          background:#ffffff; border-radius:18px;
          padding:clamp(20px, 6vw, 36px) clamp(20px, 9vw, 56px); text-align:center;
          max-width:calc(100vw - 32px); box-sizing:border-box;
          box-shadow:0 12px 40px rgba(0,0,0,0.45);
          font-family:{APP_FONT_FAMILY};
          transition: opacity 0.8s ease-out;
      ">
        <div id="tm-label" style="font-size:15px; color:#555555; font-weight:700; margin-bottom:10px; letter-spacing:1px;">
          조선시대로 이동 중<span id="tm-dots" style="display:inline-block; width:1.2em; text-align:left;">.</span>
        </div>
        <div id="tm-counter" style="font-size:clamp(26px, 9vw, 44px); font-weight:900; color:#111111; letter-spacing:1px; white-space:nowrap;">
          {start_value:,}
        </div>

        <div id="tm-note" style="
            position:absolute; top:100%; left:50%;
            transform:translateX(-50%); margin-top:14px;
            background:#ffffff; color:#222222;
            border-radius:10px; padding:7px 16px;
            font-size:13px; font-weight:700; letter-spacing:0.5px;
            width:max-content; max-width:calc(100vw - 32px); box-sizing:border-box;
            text-align:center;
            box-shadow:0 6px 20px rgba(0,0,0,0.35);
        ">
          스토커는 왼손으로 감옥의 문을 열었다
        </div>
      </div>
    </div>
    <script>
      (function() {{
        // 모바일 Streamlit 상위 컨테이너 흰색 박스 현상 방지
        var frame = window.frameElement;
        if (frame) {{
          frame.style.position = 'fixed';
          frame.style.top = '0';
          frame.style.left = '0';
          frame.style.width = '100vw';
          frame.style.height = '100vh';
          // iOS Safari 의 100vh 는 주소창 뒤까지 포함해서 화면보다 커진다. dvh 를 지원하면 덮어쓴다.
          frame.style.height = '100dvh';
          frame.style.zIndex = '999999';
          frame.style.border = 'none';
          frame.style.background = 'transparent';

          var p = frame.parentElement;
          while (p && p !== window.parent.document.body) {{
            p.style.background = 'transparent';
            p.style.border = 'none';
            p.style.padding = '0';
            p.style.margin = '0';
            p = p.parentElement;
          }}
        }}
        document.documentElement.style.background = 'transparent';
        document.body.style.background = 'transparent';
        document.body.style.margin = '0';

        var start = {start_value};
        var end = {end_value};
        var duration = {duration_ms};
        var counterEl = document.getElementById('tm-counter');
        var labelEl = document.getElementById('tm-label');
        var bgFadeEl = document.getElementById('tm-bg-fade');
        var cardEl = document.getElementById('tm-card');
        var startTime = null;

        // "이동 중" 뒤의 점이 . → .. → ... → . 순서로 계속 반복된다. (도착하면 멈춘다.)
        // 브라우저 탭(헤더) 제목도 같은 문구로 바꾼다. 다른 쪽(Streamlit)이 제목을 바꾸면 더 이상 덮어쓰지 않는다.
        var dotsEl = document.getElementById('tm-dots');
        var parentDoc = null;
        try {{ parentDoc = window.parent.document; }} catch (e) {{}}
        var lastTitle = null;
        var dotCount = 1;
        function drawDots() {{
          var dots = '.'.repeat(dotCount);
          if (dotsEl) dotsEl.textContent = dots;
          if (parentDoc) {{
            if (lastTitle !== null && parentDoc.title !== lastTitle) {{ parentDoc = null; return; }}
            lastTitle = '조선시대로 이동 중' + dots;
            parentDoc.title = lastTitle;
          }}
        }}
        drawDots();
        var dotTimer = setInterval(function() {{
          dotCount = dotCount % 3 + 1;
          drawDots();
        }}, 400);

        function easeOutExpo(t) {{
          return t >= 1 ? 1 : 1 - Math.pow(2, -10 * t);
        }}

        function formatNumber(n) {{
          return Math.round(n).toLocaleString('ko-KR');
        }}

        function step(timestamp) {{
          if (startTime === null) startTime = timestamp;
          var elapsed = timestamp - startTime;
          var t = Math.min(elapsed / duration, 1);
          var eased = easeOutExpo(t);
          var value = start + (end - start) * eased;

          bgFadeEl.style.opacity = String(eased);

          if (t < 1) {{
            counterEl.textContent = formatNumber(value);
            requestAnimationFrame(step);
          }} else {{
            counterEl.textContent = String(end);
            clearInterval(dotTimer);
            if (parentDoc && (lastTitle === null || parentDoc.title === lastTitle)) {{
              parentDoc.title = '현재 위치 : 조선시대';
            }}
            labelEl.textContent = '도착';
            bgFadeEl.style.opacity = '1';
            
            // 도착 후 중앙 카드가 자연스럽게 사라져 조선 시대 배경만 노출
            setTimeout(function() {{
              if (cardEl) cardEl.style.opacity = '0';
            }}, 1200);
          }}
        }}
        requestAnimationFrame(step);
      }})();
    </script>
    """


# ==========================================
# 고등학교 교과서 단원별 문제
#  - 고1: 공통수학1·2 (다항식, 방정식과 부등식, 행렬, 경우의 수 / 도형의 방정식, 집합과 명제, 함수와 그래프)
#  - 고2: 수학Ⅰ·수학Ⅱ (지수함수와 로그함수, 삼각함수, 수열 / 함수의 극한과 연속, 미분, 적분)
#  - 고3: 확률과 통계 (순열과 조합, 확률, 통계)
#  - 학년 버튼을 누르면 그 학년의 단원 중 '서로 다른 3개 단원'을 무작위로 뽑고, 단원마다 문제 유형(함수) 하나를
#    무작위로 뽑는다. 정답은 언제나 15, 9, 2 (타임머신 암호 '1592' 유지).
#  - 문제 함수는 모두 (target, level) 을 받는다. level: 1=하, 2=중, 3=상. 난이도는 식의 길이가 아니라
#    풀이 단계(조건의 수, 계산 과정)로 올렸다.
#  - 답(target)에서 거꾸로 수를 정하거나, 조건을 무작위로 뽑아 '직접 계산한 값'이 target 과 같을 때만 쓴다(_roll).
# ==========================================
EXPR_MAX_LEN = 30   # 문제에 나오는 식 하나의 '보이는 글자 수' 상한 (휴대폰에서도 두 줄 안에 읽히도록)


def _vis_len(tex):
    """수식이 화면에서 차지하는 글자 수. 공백·중괄호·^ 는 세지 않고, \\times 같은 기호는 한 글자로 센다."""
    n, i = 0, 0
    while i < len(tex):
        ch = tex[i]
        if ch == "\\":
            j = i + 1
            while j < len(tex) and tex[j].isalpha():
                j += 1
            n += 0 if tex[i + 1:j] in ("frac", "dfrac", "sqrt", "left", "right", "overline", "mathrm") else 1
            i = j
            continue
        if ch not in " {}^":
            n += 1
        i += 1
    return n


def _roll(make, solve, target, tries=60000):
    """make() 로 무작위 조건을 뽑고 solve(조건) 이 target 과 같을 때까지 다시 뽑는다."""
    for _ in range(tries):
        spec = make()
        if spec is not None and solve(spec) == target:
            return spec
    raise RuntimeError(f"조건을 만족하는 문제를 만들지 못했습니다 (target={target})")


def _sgn(n):
    """식에서 항 앞에 붙일 부호 문자열. 3 -> '+ 3', -3 -> '- 3', 0 -> ''."""
    return f"+ {n}" if n > 0 else (f"- {abs(n)}" if n < 0 else "")


def _coef(n, var="x"):
    """계수 n 과 문자를 이어 쓴다. 1 -> 'x', -1 -> '-x', 3 -> '3x'."""
    if n == 1:
        return var
    if n == -1:
        return f"-{var}"
    return f"{n}{var}"


def _tx(n, var="x"):
    """항 하나를 부호와 함께. 3 -> '+ 3x', -1 -> '- x'. 0 이면 빈 문자열."""
    if n == 0:
        return ""
    mag = "" if abs(n) == 1 else str(abs(n))
    return f"{'+' if n > 0 else '-'} {mag}{var}"


def _lin(a, b, var="x"):
    """일차식 ax+b. (3, -4) -> '3x - 4', (1, 0) -> 'x'."""
    return f"{_coef(a, var)} {_sgn(b)}".strip()


def _p(n):
    """음수만 괄호로 묶는다. -3 -> '(-3)', 3 -> '3'."""
    return f"({n})" if n < 0 else str(n)


def _fac(expr):
    """곱의 인수로 쓰는 식. 문자 하나뿐이면 괄호 없이 쓴다. 'x' -> 'x', 'x + 3' -> '(x + 3)', '2x' -> '(2x)'."""
    return expr if len(expr) == 1 and expr.isalpha() else f"({expr})"


def _pt(expr):
    """부호가 붙은 항만 괄호로 묶는다. '3x' -> '3x', '-x' -> '(-x)'."""
    return f"({expr})" if expr.startswith("-") else expr


def _fprod(u, v):
    """두 일차식의 곱. 같은 식이면 제곱으로 쓴다. ('x + 3', 'x + 3') -> '(x + 3)^{2}'"""
    return f"({u})^{{2}}" if u == v else f"{_fac(u)}{_fac(v)}"


def _j(*parts):
    """비어 있지 않은 조각만 공백 하나로 이어 붙인다. (항이 0 이라 빠져도 공백이 두 칸이 되지 않게)"""
    return " ".join(s for s in parts if s)


def _frac(n, d=1):
    """기약분수 \\frac{n}{d}. 정수면 그냥 정수. 음수는 앞에 -."""
    f = Fraction(n, d)
    if f.denominator == 1:
        return str(f.numerator)
    sign = "-" if f < 0 else ""
    return rf"{sign}\frac{{{abs(f.numerator)}}}{{{f.denominator}}}"


def _nonzero(lo, hi):
    return random.choice([n for n in range(lo, hi + 1) if n])


def _sq(var, c):
    """(x-c)^2 꼴. c 가 0 이면 x^2."""
    return f"{var}^{{2}}" if c == 0 else f"({var} {_sgn(-c)})^{{2}}"


def _mat(a, b, c, d):
    return rf"\begin{{pmatrix}}{a}&{b}\\{c}&{d}\end{{pmatrix}}"


# 수식($...$) 바로 뒤에 붙는 조사를 수식의 끝소리에 맞게 고친다.
#  - 문제 문장은 조사를 미리 정해 두고 숫자만 바꿔 끼우므로 "$x + 3$로", "$6$와 $8$" 같은 어색한 말이 생긴다.
#  - 끝소리를 확실히 알 수 있을 때(숫자, 영문자, 괄호로 끝남, 거듭제곱)만 고치고, 모르면 그대로 둔다.
#  - "이다/이므로" 처럼 조사 뒤에 한글이 바로 이어지면 서술격 조사이므로 건드리지 않는다.
_DIGIT_END = {"0": "C", "1": "L", "2": "V", "3": "C", "4": "V", "5": "V", "6": "C", "7": "L", "8": "L", "9": "V"}
_LETTER_END = {"l": "L", "r": "L", "m": "C", "n": "C"}      # 영문자 이름: 엘·알(ㄹ), 엠·엔(받침), 나머지는 모음
_JOSA_PAIRS = {
    "을": ("을", "를"), "를": ("을", "를"), "은": ("은", "는"), "는": ("은", "는"),
    "이": ("이", "가"), "가": ("이", "가"), "과": ("과", "와"), "와": ("과", "와"),
    "으로": ("으로", "로"), "로": ("으로", "로"),
}


def _brace_group(s, i):
    """s[i] 가 '{' 이면 짝이 맞는 '}' 까지의 (안쪽 문자열, 다음 위치) 를, 아니면 None 을 돌려준다."""
    if i >= len(s) or s[i] != "{":
        return None
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
    return None


def _tail_group(s):
    """수식이 분수·근호·선분(\\frac, \\sqrt, \\overline)으로 끝나면 읽을 때 마지막에 소리 나는 부분을 돌려준다.

    분수는 'b분의 a' 로 읽으므로 분자 a, 근호는 '루트 a' 라서 a, 선분은 그 이름이다."""
    for m in re.finditer(r"\\(?:d?frac|sqrt|overline)(?=\{)", s):
        first = _brace_group(s, m.end())
        if first is None:
            continue
        if "frac" in m.group(0):
            second = _brace_group(s, first[1])
            if second is not None and second[1] == len(s):
                return first[0]
        elif first[1] == len(s):
            return first[0]
    return None


def _end_sound(tex):
    """수식을 소리 내어 읽었을 때의 끝소리. 'V'=받침 없음, 'C'=받침 있음, 'L'=ㄹ 받침, 모르면 None."""
    s = re.sub(r"\\(?:right|left)\s*", "", tex).strip()
    while s and s[-1] in ")] ":
        s = s[:-1]                       # 괄호는 읽지 않는다. f(x) -> x, (4, 1) -> 1
    if not s:
        return None
    if s.endswith("\\%"):
        return "V"                       # 퍼센트
    if s.endswith("}"):
        if re.search(r"\^\{[^{}]*\}$", s):
            return "C"                   # 거듭제곱은 '제곱'으로 끝난다.
        inner = _tail_group(s)           # 분수는 분자, 근호는 근호 안, 선분은 그 이름으로 끝난다.
        return _end_sound(inner) if inner is not None else None
    cmd = re.search(r"\\([A-Za-z]+)$", s)
    if cmd:
        return "V" if cmd.group(1) in ("pi", "alpha", "beta", "sigma", "theta", "circ") else None
    ch = s[-1]
    if ch.isdigit():
        return _DIGIT_END[ch]
    if ch.isascii() and ch.isalpha():
        return _LETTER_END.get(ch.lower(), "V")
    return None


def _tidy_math(text):
    """수식($...$, $$...$$) 안의 연속 공백을 하나로 줄이고, 한 줄 수식($...$)의 앞뒤 빈칸을 없앤다.
    조각을 이어 붙이다 생긴 빈칸이다. ($f(x)=x^{2} $ 처럼 닫는 $ 앞에 빈칸이 있으면 수식으로 읽히지 않는 화면이 있다.)"""

    def fix(m):
        s = re.sub(r" {2,}", " ", m.group(0))
        return s if s.startswith("$$") else "$" + s[1:-1].strip() + "$"

    return re.sub(r"\$\$.*?\$\$|\$[^$]+\$", fix, text, flags=re.S)


def _fix_josa(text):
    """문장 안의 '$수식$조사' 를 받침 유무에 맞게 고친다. ($$...$$ 는 건드리지 않는다.)"""
    def repl(m):
        if m.group(1):
            return m.group(0)
        math_part, josa = m.group(2), m.group(3)
        kind = _end_sound(math_part[1:-1])
        if kind is None:
            return m.group(0)
        with_c, without_c = _JOSA_PAIRS[josa]
        if josa in ("으로", "로"):
            return math_part + ("으로" if kind == "C" else "로")
        return math_part + (with_c if kind in ("C", "L") else without_c)

    return re.sub(
        r"(\$\$.*?\$\$)|(\$[^$]+\$)(으로|로|을|를|은|는|이|가|과|와)(?![가-힣])", repl, text, flags=re.S
    )


def _pow2(e):
    """2 의 e 제곱을 식으로. 0 -> 1, 1 -> 2 (2^{1} 처럼 지수 1 을 쓰지 않는다)."""
    return "1" if e == 0 else ("2" if e == 1 else f"2^{{{e}}}")


def _poly(cs, var="x"):
    """계수 목록(높은 차수부터)을 다항식 문자열로. [1, -3, 2] -> 'x^{2} - 3x + 2'."""
    deg = len(cs) - 1
    parts = []
    for i, c in enumerate(cs):
        e = deg - i
        if c == 0:
            continue
        mag = abs(c)
        if e == 0:
            body = str(mag)
        else:
            body = ("" if mag == 1 else str(mag)) + var + (f"^{{{e}}}" if e > 1 else "")
        parts.append(("-" if c < 0 else "+", body))
    if not parts:
        return "0"
    s = ("-" if parts[0][0] == "-" else "") + parts[0][1]
    for sign, body in parts[1:]:
        s += f" {sign} {body}"
    return s


# ==========================================
# [고1] 공통수학1·2
# ==========================================
# ---------- 다항식 ----------
def h_remainder(target, level):
    """나머지정리 (하: 이차식에서 상수 구하기, 중: 삼차식에서 상수 구하기, 상: 두 조건으로 계수 구하기)."""
    if level == 1:
        while True:
            a, c = _nonzero(-3, 4), _nonzero(-9, 9)
            R = a * a + target * a + c
            if abs(R) <= 40:
                break
        q = (
            f"다항식 $f(x)=x^{{2}}+kx {_sgn(c)}$를 $x {_sgn(-a)}$로 나눈 나머지가 ${R}$일 때, "
            "상수 $k$의 값을 구하시오."
        )
        exp = (
            f"나머지정리에 의하여 $f({a})={R}$입니다.\n\n"
            f"$${_p(a)}^{{2}} {_tx(a, 'k')} {_sgn(c)} = {R}$$\n\n"
            f"$${_coef(a, 'k')} = {R - a * a - c}$$\n\n따라서 $k={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            a, p, c = _nonzero(-2, 3), _nonzero(-6, 6), _nonzero(-6, 6)
            R = a ** 3 + p * a * a + target * a + c
            if abs(R) <= 60:
                break
        q = (
            f"다항식 $f(x)=x^{{3}} {_tx(p, 'x^{2}')} + kx {_sgn(c)}$를 $x {_sgn(-a)}$로 나눈 나머지가 "
            f"${R}$일 때, 상수 $k$의 값을 구하시오."
        )
        exp = (
            f"나머지정리에 의하여 $f({a})={R}$입니다.\n\n"
            f"$${a ** 3} {_sgn(p * a * a)} {_tx(a, 'k')} {_sgn(c)} = {R}$$\n\n"
            f"$${_coef(a, 'k')} = {R - a ** 3 - p * a * a - c}$$\n\n따라서 $k={target}$"
        )
        return q, exp, str(target)
    while True:
        c0 = _nonzero(-6, 6)
        a = target
        b = -1 - a - c0
        R = 6 + 2 * a - c0
        if b != 0 and abs(b) <= 25 and abs(R) <= 60:
            break
    s1, s2 = -1 - c0, R - 8 - c0
    q = (
        f"다항식 $f(x)=x^{{3}}+ax^{{2}}+bx {_sgn(c0)}$가 $x-1$로 나누어떨어지고, "
        f"$x-2$로 나눈 나머지가 ${R}$일 때, 상수 $a$의 값을 구하시오."
    )
    exp = (
        f"$f(1)=0$이므로 $1+a+b {_sgn(c0)}=0$, 즉 $a+b={s1}$ …①\n\n"
        f"$f(2)={R}$이므로 $8+4a+2b {_sgn(c0)}={R}$, 즉 $4a+2b={s2}$ …②\n\n"
        f"②에서 ①의 2배를 빼면 $2a={s2 - 2 * s1}$이므로 $a={target}$"
    )
    return q, exp, str(target)


def h_expand_coef(target, level):
    """다항식의 전개 (하: 두 일차식의 곱에서 x의 계수, 중: 이차식과 일차식의 곱에서 x의 계수, 상: 세 일차식 또는 두 이차식의 곱에서 한 항의 계수)."""
    if level == 1:
        while True:
            p = _nonzero(-9, 12)
            r = target - p
            if r != 0 and abs(r) <= 12:
                break
        e = f"(x {_sgn(p)})(x {_sgn(r)})"
        q = f"다음 식을 전개하였을 때 $x$의 계수를 구하시오.\n\n$${e}$$"
        exp = (
            f"$$(x {_sgn(p)})(x {_sgn(r)}) = x^{{2}} {_tx(p + r)} {_sgn(p * r)}$$\n\n"
            f"따라서 $x$의 계수는 ${target}$"
        )
        return q, exp, str(target)
    if level == 2:
        # (x^2 + px + c)(x + r) 의 x 의 계수: c + p·r  (x 항이 나오는 경우가 두 가지)
        def make():
            return _nonzero(-6, 6), _nonzero(-9, 9), _nonzero(-6, 6)

        p, c, r = _roll(make, lambda v: v[1] + v[0] * v[2], target)
        e = f"({_poly([1, p, c])})(x {_sgn(r)})"
        q = f"다음 식을 전개하였을 때 $x$의 계수를 구하시오.\n\n$${e}$$"
        exp = (
            f"$x$항은 ${_pt(_coef(p))}\\times{_p(r)}$과 ${c}\\times x$에서 나오므로\n\n"
            f"$x$의 계수는 ${_p(p)}\\times{_p(r)}+{_p(c)}={target}$"
        )
        return q, exp, str(target)

    if random.random() < 0.5:
        def make():
            return [_nonzero(-6, 6) for _ in range(3)]

        p, r, s = _roll(make, lambda v: v[0] * v[1] + v[1] * v[2] + v[2] * v[0], target)
        e = f"(x {_sgn(p)})(x {_sgn(r)})(x {_sgn(s)})"
        q = f"다음 식을 전개하였을 때 $x$의 계수를 구하시오.\n\n$${e}$$"
        exp = (
            "$(x+a)(x+b)(x+c)=x^{3}+(a+b+c)x^{2}+(ab+bc+ca)x+abc$이므로\n\n"
            f"$x$의 계수는 ${_p(p)}\\times{_p(r)}+{_p(r)}\\times{_p(s)}+{_p(s)}\\times{_p(p)}={target}$"
        )
        return q, exp, str(target)

    # (x^2 + px + c)(x^2 + rx + s) 의 x^2 의 계수: s + p·r + c  (x^2 항이 나오는 경우가 세 가지)
    def make2():
        return _nonzero(-5, 5), _nonzero(-6, 6), _nonzero(-5, 5), _nonzero(-6, 6)

    p, c, r, s = _roll(make2, lambda v: v[3] + v[0] * v[2] + v[1], target)
    e = f"({_poly([1, p, c])})({_poly([1, r, s])})"
    q = f"다음 식을 전개하였을 때 $x^{{2}}$의 계수를 구하시오.\n\n$${e}$$"
    exp = (
        f"$x^{{2}}$항은 $x^{{2}}\\times{_p(s)}$, ${_pt(_coef(p))}\\times{_pt(_coef(r))}$, ${c}\\times x^{{2}}$에서 나오므로\n\n"
        f"$x^{{2}}$의 계수는 ${s}+{_p(p)}\\times{_p(r)}+{_p(c)}={target}$"
    )
    return q, exp, str(target)


# ---------- 방정식과 부등식 ----------
def h_quad_roots(target, level):
    """이차방정식의 근과 계수의 관계 (하: 두 근의 차, 중: 두 근의 제곱의 합, 상: 새 이차방정식의 상수항)."""
    if level == 1:
        if random.random() < 0.5:
            # 두 근의 차: 두 근이 모두 양수인 이차방정식
            pairs = [(target // d, d) for d in range(1, target + 1) if target % d == 0 and target // d > d]
            al, be = random.choice(pairs)
            p, d = al + be, al - be
            q = (
                f"이차방정식 $x^{{2}}-{p}x+k=0$의 두 근의 차가 ${d}$일 때, 상수 $k$의 값을 구하시오. "
                "(단, 두 근은 모두 양수이다.)"
            )
            exp = (
                "두 근을 $\\alpha$, $\\beta$ ($\\alpha>\\beta$)라 하면\n\n"
                f"$$\\alpha+\\beta={p},\\quad \\alpha-\\beta={d}$$\n\n"
                f"이므로 $\\alpha={al}$, $\\beta={be}$입니다. 근과 계수의 관계에서 $k=\\alpha\\beta={target}$"
            )
            return q, exp, str(target)
        # 한 근이 주어진 경우: k = r(p - r)
        r = random.choice([d for d in range(-target, target + 1) if d and target % d == 0])
        p = r + target // r
        q = (
            f"이차방정식 $x^{{2}} {_tx(-p)} + k=0$의 한 근이 ${r}$일 때, 상수 $k$의 값을 구하시오."
        )
        other = p - r
        exp = (
            f"$x={r}$을 대입하면 ${r * r} {_sgn(-p * r)} + k=0$이므로 $k={p * r - r * r}$입니다.\n\n"
            f"(다른 한 근은 근과 계수의 관계에서 ${p}-{_p(r)}={other}$이므로 $k={r}\\times{_p(other)}$입니다.)"
        )
        return q, exp, str(target)
    if level == 2:
        lo = math.isqrt(4 * target) + 1       # p^2 > 4k 라서 두 근이 실수
        p = random.randint(lo, lo + 2)        # 너무 큰 수가 나오지 않게 좁게 잡는다.
        S = p * p - 2 * target
        q = (
            f"이차방정식 $x^{{2}}-{p}x+k=0$의 두 근을 $\\alpha$, $\\beta$라 할 때, "
            f"$\\alpha^{{2}}+\\beta^{{2}}={S}$이다. 상수 $k$의 값을 구하시오."
        )
        exp = (
            f"근과 계수의 관계에 의하여 $\\alpha+\\beta={p}$, $\\alpha\\beta=k$입니다.\n\n"
            f"$$\\alpha^{{2}}+\\beta^{{2}}=(\\alpha+\\beta)^{{2}}-2\\alpha\\beta={p}^{{2}}-2k={S}$$\n\n"
            f"따라서 $k={target}$"
        )
        return q, exp, str(target)
    while True:
        s = random.randint(3, 12)
        m = target - s - 1
        if m != 0 and s * s >= 4 * m:
            break
    q = (
        f"이차방정식 $x^{{2}}-{s}x {_sgn(m)}=0$의 두 근을 $\\alpha$, $\\beta$라 할 때, "
        "$\\alpha+1$, $\\beta+1$을 두 근으로 하는 이차방정식이 $x^{2}+px+q=0$이다. "
        "상수 $q$의 값을 구하시오."
    )
    exp = (
        f"근과 계수의 관계에 의하여 $\\alpha+\\beta={s}$, $\\alpha\\beta={m}$입니다.\n\n"
        f"$q=(\\alpha+1)(\\beta+1)=\\alpha\\beta+(\\alpha+\\beta)+1={_p(m)}+{s}+1={target}$"
    )
    return q, exp, str(target)


def h_quad_ineq_count(target, level):
    """이차부등식을 만족시키는 정수의 개수 (하: 인수분해된 꼴, 중: 전개된 꼴, 상: 연립부등식)."""
    if level == 1:
        while True:
            a = _nonzero(-8, 4)
            b = a + target + 1
            if b != 0 and abs(b) <= 18:
                break
        q = (
            "다음 부등식을 만족시키는 정수 $x$의 개수를 구하시오.\n\n"
            f"$${_fprod(_lin(1, -a), _lin(1, -b))}<0$$"
        )
        exp = (
            f"부등식의 해는 ${a}<x<{b}$입니다.\n\n"
            f"이 범위의 정수는 ${a + 1}$부터 ${b - 1}$까지이므로 ${b - 1}-{_p(a + 1)}+1={target}$(개)"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            a = _nonzero(-8, 4)
            closed = random.random() < 0.5
            b = a + target - 1 if closed else a + target + 1
            if b != 0 and abs(b) <= 18 and b > a:
                break
        op, op_tex = ("\\le", "\\le") if closed else ("<", "<")
        e = _poly([1, -(a + b), a * b])
        q = (
            "다음 부등식을 만족시키는 정수 $x$의 개수를 구하시오.\n\n"
            f"$${e} {op} 0$$"
        )
        lo, hi = (a, b) if closed else (a + 1, b - 1)
        exp = (
            f"좌변을 인수분해하면 ${_fprod(_lin(1, -a), _lin(1, -b))} {op_tex} 0$이므로 "
            f"해는 ${a} {op} x {op} {b}$입니다.\n\n"
            f"이 범위의 정수는 ${lo}$부터 ${hi}$까지이므로 ${target}$개"
        )
        return q, exp, str(target)

    def make():
        a = random.randint(-9, 3)
        b = a + random.randint(3, 20)
        c = random.randint(-6, 8)
        d = random.randint(2, 12)
        return a, b, c, d

    def solve(s):
        a, b, c, d = s
        xs = [x for x in range(-40, 41) if (x - a) * (x - b) < 0 and abs(x - c) <= d]
        if not xs or xs != list(range(xs[0], xs[-1] + 1)):
            return None
        return len(xs)

    a, b, c, d = _roll(make, solve, target)
    xs = [x for x in range(-40, 41) if (x - a) * (x - b) < 0 and abs(x - c) <= d]
    q = (
        "다음 연립부등식을 만족시키는 정수 $x$의 개수를 구하시오.\n\n"
        f"$$\\begin{{cases}}{_fprod(_lin(1, -a), _lin(1, -b))}<0\\\\|{_lin(1, -c)}|\\le {d}\\end{{cases}}$$"
    )
    exp = (
        f"첫째 부등식의 해는 ${a}<x<{b}$입니다.\n\n"
        f"둘째 부등식은 ${c - d}\\le x\\le {c + d}$입니다.\n\n"
        f"공통 범위는 ${xs[0]}\\le x\\le {xs[-1]}$이므로 정수 $x$는 ${target}$개"
    )
    return q, exp, str(target)


# ---------- 행렬 ----------
def h_matrix(target, level):
    """행렬 (하: 곱의 한 성분, 중: 곱의 두 대각성분의 합, 상: 거듭제곱의 성분으로 상수 구하기)."""
    if level == 3:
        n = random.choice([2, 3, 4, 5])
        N = n * target
        upper = random.random() < 0.5
        base = _mat(1, "a", 0, 1) if upper else _mat(1, 0, "a", 1)
        pos = "(1,2)" if upper else "(2,1)"

        def power(k):
            return _mat(1, f"{k}a", 0, 1) if upper else _mat(1, 0, f"{k}a", 1)

        q = (
            "행렬 $A=" + base + f"$에 대하여 $A^{{{n}}}$의 ${pos}$ 성분이 ${N}$일 때, "
            "상수 $a$의 값을 구하시오."
        )
        exp = (
            "$A^{2}=" + power(2) + "$, $A^{3}=" + power(3)
            + "$이므로 일반적으로 $A^{n}=" + power("n") + "$입니다.\n\n"
            f"${pos}$ 성분은 ${n}a={N}$이므로 $a={target}$"
        )
        return q, exp, str(target)

    def make():
        A = [[_nonzero(-4, 6) for _ in range(2)] for _ in range(2)]
        B = [[_nonzero(-4, 6) for _ in range(2)] for _ in range(2)]
        i, j = random.randrange(2), random.randrange(2)
        return A, B, i, j

    if level == 1:
        def solve(s):
            A, B, i, j = s
            return A[i][0] * B[0][j] + A[i][1] * B[1][j]

        A, B, i, j = _roll(make, solve, target)
        q = (
            "두 행렬\n\n"
            + rf"$$A={_mat(A[0][0], A[0][1], A[1][0], A[1][1])},\quad B={_mat(B[0][0], B[0][1], B[1][0], B[1][1])}$$"
            + f"\n\n에 대하여 행렬 $AB$의 $({i + 1},{j + 1})$ 성분을 구하시오."
        )
        exp = (
            f"$AB$의 $({i + 1},{j + 1})$ 성분은 $A$의 ${i + 1}$행과 $B$의 ${j + 1}$열의 성분을 차례로 곱하여 더한 값이므로\n\n"
            f"$${_p(A[i][0])}\\times{_p(B[0][j])}+{_p(A[i][1])}\\times{_p(B[1][j])}={target}$$"
        )
        return q, exp, str(target)

    def solve2(s):
        A, B, _, _ = s
        return A[0][0] * B[0][0] + A[0][1] * B[1][0] + A[1][0] * B[0][1] + A[1][1] * B[1][1]

    A, B, _, _ = _roll(make, solve2, target)
    q = (
        "두 행렬\n\n"
        + rf"$$A={_mat(A[0][0], A[0][1], A[1][0], A[1][1])},\quad B={_mat(B[0][0], B[0][1], B[1][0], B[1][1])}$$"
        + "\n\n에 대하여 행렬 $AB$의 $(1,1)$ 성분과 $(2,2)$ 성분의 합을 구하시오."
    )
    c11 = A[0][0] * B[0][0] + A[0][1] * B[1][0]
    c22 = A[1][0] * B[0][1] + A[1][1] * B[1][1]
    exp = (
        f"$AB$의 $(1,1)$ 성분은 ${_p(A[0][0])}\\times{_p(B[0][0])}+{_p(A[0][1])}\\times{_p(B[1][0])}={c11}$,\n\n"
        f"$(2,2)$ 성분은 ${_p(A[1][0])}\\times{_p(B[0][1])}+{_p(A[1][1])}\\times{_p(B[1][1])}={c22}$이므로\n\n"
        f"두 성분의 합은 ${_p(c11)}+{_p(c22)}={target}$"
    )
    return q, exp, str(target)


# ---------- 경우의 수 ----------
_MULT_CACHE = {}


def _mult_solutions(kind, target):
    """배수의 개수 문제에서 정답이 target 이 되는 (수들, N) 목록. 한 번 구해 두고 다시 쓴다."""
    key = (kind, target)
    if key in _MULT_CACHE:
        return _MULT_CACHE[key]
    out = []
    # N 은 세 난이도 모두 80 이하로 둔다. (N 이 100 을 넘으면 배수를 하나씩 세는 부담이 단원 안에서 너무 커진다.)
    if kind == "or2":            # a 또는 b 의 배수
        for a in range(2, 10):
            for b in range(a + 1, 10):
                for N in range(5, 81):
                    if b % a and N >= b and N // a + N // b - N // math.lcm(a, b) == target:
                        out.append(((a, b), N))
    elif kind == "only":         # a 의 배수이지만 b 의 배수는 아닌 것
        for a in range(2, 10):
            for b in range(2, 10):
                if a == b or a % b == 0:
                    continue
                for N in range(5, 81):
                    if N // a - N // math.lcm(a, b) == target:
                        out.append(((a, b), N))
    elif kind == "or3":          # 세 수 중 적어도 하나의 배수 (한 수가 다른 수의 약수이면 문제가 싱거워지므로 뺀다)
        for nums in itertools.combinations([2, 3, 4, 5, 6, 7, 9], 3):
            if any(x != y and y % x == 0 for x in nums for y in nums):
                continue
            for N in range(10, 61):
                if sum(1 for k in range(1, N + 1) if any(k % m == 0 for m in nums)) == target:
                    out.append((nums, N))
    else:                        # 두 수 어느 것의 배수도 아닌 것
        for a in range(2, 8):
            for b in range(a + 1, 9):
                for N in range(5, 61):
                    if b % a and N - (N // a + N // b - N // math.lcm(a, b)) == target:
                        out.append(((a, b), N))
    _MULT_CACHE[key] = out
    return out


def h_count_multiples(target, level):
    """배수의 개수 (하: a 또는 b 의 배수, 중: a 의 배수이면서 b 의 배수가 아닌 것, 상: 세 수 중 하나 이상의 배수 / 두 수 어느 것의 배수도 아닌 것)."""
    if level == 1:
        (a, b), N = random.choice(_mult_solutions("or2", target))
        q = f"$1$부터 ${N}$까지의 자연수 중에서 ${a}$의 배수 또는 ${b}$의 배수의 개수를 구하시오."
        inter = N // math.lcm(a, b)
        if inter:
            both = f"${a}$와 ${b}$의 공배수(${math.lcm(a, b)}$의 배수)는 ${inter}$개입니다."
            tail = f"$n(A\\cup B)=n(A)+n(B)-n(A\\cap B)={N // a}+{N // b}-{inter}={target}$"
        else:
            both = f"${N}$ 이하에는 ${a}$와 ${b}$의 공배수가 없습니다."
            tail = f"$n(A\\cup B)=n(A)+n(B)={N // a}+{N // b}={target}$"
        exp = (
            f"${a}$의 배수는 ${N // a}$개, ${b}$의 배수는 ${N // b}$개입니다. {both}\n\n{tail}"
        )
        return q, exp, str(target)
    if level == 2:
        (a, b), N = random.choice(_mult_solutions("only", target))
        q = f"$1$부터 ${N}$까지의 자연수 중에서 ${a}$의 배수이지만 ${b}$의 배수는 아닌 수의 개수를 구하시오."
        inter = N // math.lcm(a, b)
        if inter:
            exp = (
                f"${a}$의 배수는 ${N // a}$개이고, 그 중 ${b}$의 배수이기도 한 수(${math.lcm(a, b)}$의 배수)는 "
                f"${inter}$개입니다.\n\n따라서 ${N // a}-{inter}={target}$(개)"
            )
        else:
            exp = (
                f"${a}$의 배수는 ${N // a}$개이고, 이 중에는 ${b}$의 배수가 없습니다.\n\n"
                f"따라서 ${N // a}={target}$(개)"
            )
        return q, exp, str(target)
    kinds = ["or3", "none2"] if _mult_solutions("or3", target) else ["none2"]
    if random.choice(kinds) == "or3":
        (a, b, c), N = random.choice(_mult_solutions("or3", target))

        def cnt(m):
            return N // m

        q = f"$1$부터 ${N}$까지의 자연수 중에서 ${a}$, ${b}$, ${c}$ 중 적어도 하나의 배수인 수의 개수를 구하시오."
        pairs = [cnt(math.lcm(a, b)), cnt(math.lcm(b, c)), cnt(math.lcm(c, a))]
        triple = cnt(math.lcm(a, b, c))
        calc = f"{cnt(a)}+{cnt(b)}+{cnt(c)}" + "".join(f"-{v}" for v in pairs if v) + (f"+{triple}" if triple else "")
        exp = (
            f"${a}$의 배수, ${b}$의 배수, ${c}$의 배수의 개수는 각각 ${cnt(a)}$, ${cnt(b)}$, ${cnt(c)}$이고,\n\n"
            f"${a}$와 ${b}$의 공배수는 ${pairs[0]}$개, ${b}$와 ${c}$의 공배수는 ${pairs[1]}$개, "
            f"${c}$와 ${a}$의 공배수는 ${pairs[2]}$개, 세 수의 공배수는 ${triple}$개입니다.\n\n"
            f"포함과 배제의 원리에 의하여 ${calc}={target}$"
        )
        return q, exp, str(target)
    (a, b), N = random.choice(_mult_solutions("none2", target))
    inter = N // math.lcm(a, b)
    either = N // a + N // b - inter
    q = f"$1$부터 ${N}$까지의 자연수 중에서 ${a}$의 배수도 아니고 ${b}$의 배수도 아닌 수의 개수를 구하시오."
    calc = f"{N // a}+{N // b}-{inter}" if inter else f"{N // a}+{N // b}"
    exp = (
        f"${a}$의 배수 또는 ${b}$의 배수의 개수는 ${calc}={either}$입니다.\n\n"
        f"따라서 어느 것의 배수도 아닌 수의 개수는 ${N}-{either}={target}$"
    )
    return q, exp, str(target)


def _dice_pairs():
    return [(i, j) for i in range(1, 7) for j in range(1, 7)]


def _is_prime_small(n):
    return n in (2, 3, 5, 7, 11)


def _dice_conditions(level):
    """(문장, 조건) 목록. 두 주사위의 눈 (i, j) 에 대한 조건이다."""
    conds = []
    if level == 1:
        for k in range(2, 13):
            conds.append((f"나오는 두 눈의 수의 합이 ${k}$", lambda i, j, k=k: i + j == k))
        for a in range(2, 13):
            for b in range(a + 1, 13):
                conds.append(
                    (f"나오는 두 눈의 수의 합이 ${a}$ 또는 ${b}$", lambda i, j, a=a, b=b: i + j in (a, b))
                )
        for a in range(2, 13):
            for b in range(a + 1, 13):
                for c in range(b + 1, 13):
                    conds.append(
                        (
                            f"나오는 두 눈의 수의 합이 ${a}$, ${b}$, ${c}$ 중 하나",
                            lambda i, j, a=a, b=b, c=c: i + j in (a, b, c),
                        )
                    )
        return conds
    if level == 2:
        for k in range(3, 12):
            conds.append((f"나오는 두 눈의 수의 합이 ${k}$ 이하", lambda i, j, k=k: i + j <= k))
            conds.append((f"나오는 두 눈의 수의 합이 ${k}$ 이상", lambda i, j, k=k: i + j >= k))
        for m in range(2, 7):
            conds.append((f"나오는 두 눈의 수의 합이 ${m}$의 배수", lambda i, j, m=m: (i + j) % m == 0))
        conds.append(("나오는 두 눈의 수의 합이 소수", lambda i, j: _is_prime_small(i + j)))
        for d in range(0, 6):
            conds.append((f"나오는 두 눈의 수의 차가 ${d}$", lambda i, j, d=d: abs(i - j) == d))
        for d in range(2, 6):
            conds.append((f"나오는 두 눈의 수의 차가 ${d}$ 이상", lambda i, j, d=d: abs(i - j) >= d))
        conds.append(("첫 번째 주사위의 눈의 수가 두 번째 주사위의 눈의 수보다 작은", lambda i, j: i < j))
        conds.append(("두 눈의 수가 모두 홀수", lambda i, j: i % 2 == 1 and j % 2 == 1))
        conds.append(("두 눈의 수가 모두 짝수", lambda i, j: i % 2 == 0 and j % 2 == 0))
        conds.append(("두 눈의 수가 모두 소수", lambda i, j: i in (2, 3, 5) and j in (2, 3, 5)))
        conds.append(("두 눈의 수가 서로 같은", lambda i, j: i == j))
        return conds
    # 상: 조건이 두 개 겹친다. (합의 조건 + 곱의 조건, 또는 곱의 범위)
    sum_conds = [(f"합이 ${k}$ 이하", lambda i, j, k=k: i + j <= k) for k in range(4, 11)]
    sum_conds += [(f"합이 ${k}$ 이상", lambda i, j, k=k: i + j >= k) for k in range(5, 11)]
    prod_conds = [("곱이 홀수", lambda i, j: (i * j) % 2 == 1), ("곱이 짝수", lambda i, j: (i * j) % 2 == 0)]
    prod_conds += [(f"곱이 ${m}$의 배수", lambda i, j, m=m: (i * j) % m == 0) for m in (3, 4, 6)]
    for s_text, s_fn in sum_conds:
        for p_text, p_fn in prod_conds:
            conds.append(
                (
                    f"나오는 두 눈의 수의 {s_text}이고 {p_text}",
                    lambda i, j, s_fn=s_fn, p_fn=p_fn: s_fn(i, j) and p_fn(i, j),
                )
            )
    for lo in range(2, 30, 2):
        for hi in range(lo + 4, 37, 2):
            conds.append(
                (
                    f"나오는 두 눈의 수의 곱이 ${lo}$ 이상 ${hi}$ 이하",
                    lambda i, j, lo=lo, hi=hi: lo <= i * j <= hi,
                )
            )
    return conds


def h_dice(target, level):
    """서로 다른 두 주사위의 눈에 대한 경우의 수 (하: 합이 정해진 값, 중: 합·차의 조건, 상: 합과 곱의 조건이 겹친 경우)."""
    ok = [
        (text, f)
        for text, f in _dice_conditions(level)
        if sum(1 for i, j in _dice_pairs() if f(i, j)) == target
    ]
    text, f = random.choice(ok)
    pairs = [(i, j) for i, j in _dice_pairs() if f(i, j)]
    tail = " 경우의 수를 구하시오." if text.endswith(("은", "는")) else "인 경우의 수를 구하시오."
    q = f"서로 다른 두 개의 주사위를 동시에 던질 때, {text}{tail}"
    if len(pairs) <= 15:
        listing = ", ".join(f"$({i}, {j})$" for i, j in pairs)
        exp = f"조건을 만족시키는 눈의 수의 순서쌍 $(i, j)$는\n\n{listing}\n\n으로 모두 ${target}$가지입니다."
    else:
        exp = f"36가지 순서쌍 $(i, j)$ 중에서 조건을 만족시키는 것을 세어 보면 모두 ${target}$가지입니다."
    return q, exp, str(target)


# ---------- 도형의 방정식 ----------
_LINE_NORMS = [(3, 4, 5), (4, 3, 5), (6, 8, 10), (8, 6, 10), (5, 12, 13), (12, 5, 13)]


def _line(p, q, c):
    return f"{_coef(p)} {_tx(q, 'y')} {_sgn(c)} = 0"


def h_distance(target, level):
    """거리 (하: 점과 직선 사이의 거리, 중: 평행한 두 직선 사이의 거리, 상: 직선이 원에 접할 조건)."""
    if level == 1:
        while True:
            p, qq, n = random.choice(_LINE_NORMS)
            a, b = random.randint(-5, 5), random.randint(-5, 5)
            sign = random.choice([1, -1])
            c = sign * n * target - p * a - qq * b
            if abs(c) <= 99 and c != 0:
                break
        q = f"점 $({a}, {b})$에서 직선 ${_line(p, qq, c)}$까지의 거리를 구하시오."
        val = p * a + qq * b + c
        exp = (
            "점과 직선 사이의 거리 공식에 의하여\n\n"
            f"$$\\frac{{|{p}\\times{_p(a)}+{qq}\\times{_p(b)} {_sgn(c)}|}}{{\\sqrt{{{p}^{{2}}+{qq}^{{2}}}}}}"
            f"=\\frac{{{abs(val)}}}{{{n}}}={target}$$"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            p, qq, n = random.choice(_LINE_NORMS)
            c1 = random.randint(-30, 30)
            c2 = c1 + random.choice([1, -1]) * n * target
            if c1 != 0 and c2 != 0 and abs(c2) <= 99:
                break
        q = f"평행한 두 직선 ${_line(p, qq, c1)}$, ${_line(p, qq, c2)}$ 사이의 거리를 구하시오."
        exp = (
            "직선 위의 한 점과 다른 직선 사이의 거리를 구하면 됩니다.\n\n"
            f"$$\\frac{{|{c1}-{_p(c2)}|}}{{\\sqrt{{{p}^{{2}}+{qq}^{{2}}}}}}=\\frac{{{abs(c1 - c2)}}}{{{n}}}={target}$$"
        )
        return q, exp, str(target)
    while True:
        p, qq, n = random.choice(_LINE_NORMS[:4])
        r = random.randint(1, 8)
        a, b = random.randint(-6, 6), random.randint(-6, 6)
        k = n * r - p * a - qq * b
        if k == target and -n * r - p * a - qq * b < 0 and (a, b) != (0, 0):
            break
    q = (
        f"직선 ${_coef(p)} {_tx(qq, 'y')} + k = 0$이 원 ${_sq('x', a)}+{_sq('y', b)}={r * r}$에 접할 때, "
        "양수 $k$의 값을 구하시오."
    )
    exp = (
        f"원의 중심 $({a}, {b})$와 직선 사이의 거리가 반지름 ${r}$과 같아야 하므로\n\n"
        f"$$\\frac{{|{p}\\times{_p(a)}+{qq}\\times{_p(b)}+k|}}{{{n}}}={r}$$\n\n"
        f"$|{p * a + qq * b}+k|={n * r}$이므로 $k={n * r - p * a - qq * b}$ 또는 $k={-n * r - p * a - qq * b}$이고, "
        f"양수는 $k={target}$"
    )
    return q, exp, str(target)


def h_circle(target, level):
    """원의 방정식 (하: 일반형에서 반지름, 중: 지름의 양 끝점에서 중심의 좌표의 합, 상: 원점에서 원 위의 점까지의 거리)."""
    if level == 1:
        while True:
            p, qq = random.randint(-6, 6), random.randint(-6, 6)
            if (p, qq) != (0, 0):
                break
        A, B, C = -2 * p, -2 * qq, p * p + qq * qq - target * target
        eq = " ".join(s for s in ("x^{2}+y^{2}", _tx(A), _tx(B, "y"), _sgn(C)) if s)
        q = f"원 ${eq}=0$의 반지름의 길이를 구하시오."
        exp = (
            "완전제곱식으로 고치면\n\n"
            f"$${_sq('x', p)}+{_sq('y', qq)}={target ** 2}$$\n\n"
            f"이므로 반지름의 길이는 ${target}$"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            a, b, c = random.randint(-8, 8), random.randint(-8, 8), random.randint(-8, 8)
            d = 2 * target - a - b - c
            if abs(d) <= 9 and (a, b) != (c, d):
                break
        q = (
            f"두 점 $A({a}, {b})$, $B({c}, {d})$를 지름의 양 끝점으로 하는 원의 중심의 $x$좌표와 "
            "$y$좌표의 합을 구하시오."
        )
        exp = (
            "원의 중심은 지름의 양 끝점의 중점입니다.\n\n"
            f"$$\\left(\\frac{{{a}+{_p(c)}}}{{2}},\\ \\frac{{{b}+{_p(d)}}}{{2}}\\right)"
            f"=\\left({_frac(a + c, 2)},\\ {_frac(b + d, 2)}\\right)$$\n\n"
            f"따라서 두 좌표의 합은 ${target}$"
        )
        return q, exp, str(target)
    triples = [(3, 4, 5), (4, 3, 5), (5, 12, 13), (12, 5, 13), (6, 8, 10), (8, 6, 10)]

    def make():
        a, b, d = random.choice(triples)
        a, b = a * random.choice([1, -1]), b * random.choice([1, -1])
        return a, b, d, random.randint(1, 12), random.choice(["max", "min"])

    def solve(s):
        a, b, d, r, mode = s
        if mode == "max":
            return d + r
        return d - r if d > r else None

    a, b, d, r, mode = _roll(make, solve, target)
    word, how = ("가장 큰", "d+r") if mode == "max" else ("가장 작은", "d-r")
    q = (
        f"원 ${_sq('x', a)}+{_sq('y', b)}={r * r}$ 위의 점과 원점 사이의 거리 중 {word} 값을 구하시오."
    )
    exp = (
        f"원의 중심 $({a}, {b})$와 원점 사이의 거리는 $d=\\sqrt{{{_p(a)}^{{2}}+{_p(b)}^{{2}}}}={d}$이고 반지름은 $r={r}$입니다.\n\n"
        + (
            f"원점은 원 {'바깥' if d > r else '안'}에 있고, 가장 큰 거리는 $d+r={d}+{r}={target}$"
            if mode == "max"
            else f"원점은 원 바깥에 있고, 가장 작은 거리는 $d-r={d}-{r}={target}$"
        )
    )
    return q, exp, str(target)


def h_line(target, level):
    """직선의 방정식 (하: 평행 조건, 중: 수직 조건, 상: 세 점이 한 직선 위에 있을 조건)."""
    if level in (1, 2):
        while True:
            p, qq = random.randint(1, 6), _nonzero(-6, 6)
            if level == 1:
                # 평행: p*a = p2*qq 이므로 a = p2*qq/p.  a 가 target 이 되도록 p2 를 거꾸로 정한다.
                if (target * p) % qq != 0:
                    continue
                p2 = target * p // qq
            else:
                # 수직: p*p2 + qq*a = 0 이므로 a = -p*p2/qq.  a 가 target 이 되도록 p2 를 거꾸로 정한다.
                if (qq * target) % p != 0:
                    continue
                p2 = -qq * target // p
            r, r2 = _nonzero(-9, 9), _nonzero(-9, 9)
            if p2 == 0 or abs(p2) > 12 or (level == 1 and p * r2 == p2 * r):
                continue
            break
        line1 = f"{_coef(p)} {_tx(qq, 'y')} {_sgn(r)} = 0"
        line2 = f"{_coef(p2)} + ay {_sgn(r2)} = 0"
        if level == 1:
            q = f"두 직선 ${line1}$, ${line2}$이 서로 평행할 때, 상수 $a$의 값을 구하시오."
            exp = (
                "두 직선이 평행하려면 $x$의 계수와 $y$의 계수의 비가 같아야 합니다.\n\n"
                f"$${p}:{qq}={p2}:a$$\n\n"
                f"${_coef(p, 'a')}={qq}\\times{_p(p2)}$이므로 $a={target}$"
            )
        else:
            q = f"두 직선 ${line1}$, ${line2}$이 서로 수직일 때, 상수 $a$의 값을 구하시오."
            exp = (
                "두 직선이 수직이려면 $x$의 계수끼리의 곱과 $y$의 계수끼리의 곱의 합이 $0$이어야 합니다.\n\n"
                f"$${p}\\times{_p(p2)}+{_p(qq)}\\times a=0$$\n\n"
                f"이므로 $a={target}$"
            )
        return q, exp, str(target)
    while True:
        x1, y1, x2 = random.randint(-4, 4), random.randint(-6, 6), random.randint(-4, 4)
        m = _nonzero(-4, 4)
        if x2 == x1:
            continue
        y2 = y1 + m * (x2 - x1)
        if (target - y1) % m != 0:
            continue
        x3 = x1 + (target - y1) // m
        if x3 in (x1, x2) or abs(x3) > 12 or abs(y2) > 15:
            continue
        break
    q = f"세 점 $A({x1}, {y1})$, $B({x2}, {y2})$, $C({x3}, k)$가 한 직선 위에 있을 때, $k$의 값을 구하시오."
    exp = (
        f"직선 $AB$의 기울기는 $\\frac{{{y2}-{_p(y1)}}}{{{x2}-{_p(x1)}}}={m}$입니다.\n\n"
        f"점 $C$도 이 직선 위에 있으므로 $\\frac{{k-{_p(y1)}}}{{{x3}-{_p(x1)}}}={m}$이고, 따라서 $k={target}$"
    )
    return q, exp, str(target)


# ---------- 집합과 명제 ----------
def h_set_count(target, level):
    """집합의 원소의 개수 (하: 합집합의 원소, 중: 합집합의 여집합의 원소, 상: 세 집합의 합집합의 여집합의 원소)."""
    if level == 1:
        # 한 집합이 다른 집합에 완전히 포함되면(n(A∩B) 가 n(A) 와 같으면) 답이 문제에 그대로 보이므로,
        # 가능하면 두 집합 모두 교집합보다 크게 잡는다. (target 이 작으면 그럴 수 없다.)
        strict = target >= 4
        both = random.randint(1, min(8, target - 2 if strict else target))
        n_a = random.randint(both + 1 if strict else both, target - 1 if strict else target)   # n(A) + n(B) = target + both
        n_b = target + both - n_a
        q = (
            f"두 집합 $A$, $B$에 대하여 $n(A)={n_a}$, $n(B)={n_b}$, $n(A\\cap B)={both}$일 때, "
            "$n(A\\cup B)$의 값을 구하시오."
        )
        exp = (
            "$n(A\\cup B)=n(A)+n(B)-n(A\\cap B)$이므로\n\n"
            f"$$n(A\\cup B)={n_a}+{n_b}-{both}={target}$$"
        )
        return q, exp, str(target)
    if level == 2:
        a_only, b_only, both = random.randint(3, 20), random.randint(3, 20), random.randint(1, 10)
        n_a, n_b = a_only + both, b_only + both
        N = a_only + b_only + both + target
        q = (
            f"전체집합 $U$의 원소의 개수가 ${N}$이고 $n(A)={n_a}$, $n(B)={n_b}$, $n(A\\cap B)={both}$일 때, "
            "$n\\left((A\\cup B)^{c}\\right)$의 값을 구하시오."
        )
        exp = (
            f"$n(A\\cup B)=n(A)+n(B)-n(A\\cap B)={n_a}+{n_b}-{both}={n_a + n_b - both}$\n\n"
            f"$n\\left((A\\cup B)^{{c}}\\right)=n(U)-n(A\\cup B)={N}-{n_a + n_b - both}={target}$"
        )
        return q, exp, str(target)
    # 벤 다이어그램의 일곱 영역(A만, B만, C만, A∩B만, B∩C만, C∩A만, 셋 모두)을 정하고, 어느 집합에도 속하지 않는 원소를 target 개로 둔다.
    while True:
        ao, bo, co, ab, bc, ca = (random.randint(1, 6) for _ in range(6))
        abc = random.randint(1, 4)
        union = ao + bo + co + ab + bc + ca + abc
        if union + target <= 60:
            break
    N = union + target
    nA, nB, nC = ao + ab + ca + abc, bo + ab + bc + abc, co + bc + ca + abc
    nAB, nBC, nCA = ab + abc, bc + abc, ca + abc
    q = (
        f"전체집합 $U$의 원소의 개수가 ${N}$이고 세 집합 $A$, $B$, $C$에 대하여 $n(A)={nA}$, $n(B)={nB}$, "
        f"$n(C)={nC}$, $n(A\\cap B)={nAB}$, $n(B\\cap C)={nBC}$, $n(C\\cap A)={nCA}$, "
        f"$n(A\\cap B\\cap C)={abc}$일 때, $n\\left((A\\cup B\\cup C)^{{c}}\\right)$의 값을 구하시오."
    )
    exp = (
        "포함과 배제의 원리에 의하여\n\n"
        f"$$n(A\\cup B\\cup C)={nA}+{nB}+{nC}-{nAB}-{nBC}-{nCA}+{abc}={union}$$\n\n"
        f"$n\\left((A\\cup B\\cup C)^{{c}}\\right)=n(U)-n(A\\cup B\\cup C)={N}-{union}={target}$"
    )
    return q, exp, str(target)


def _elements(vals):
    """원소 목록을 '$2$, $5$를' 처럼 조사까지 붙여서 쓴다."""
    return ", ".join(f"${v}$" for v in vals) + ("을" if _DIGIT_END[str(vals[-1])[-1]] in ("C", "L") else "를")


def h_subset(target, level):
    """부분집합의 개수 (하: 특정 원소를 반드시 포함, 중: 특정 원소는 포함하고 다른 원소는 제외, 상: 원소의 개수가 홀수·짝수인 부분집합).

    부분집합의 개수는 $2^{m}$ 꼴(지수 m 만)로 준다. ($2^{15}=32768$ 같은 큰 수를 문제에 쓰지 않으려고.)
    """
    univ = "$A=\\{1, 2, \\cdots, n\\}$"   # 정답이 n=2 인 경우에도 어색하지 않게 3 을 적지 않는다.
    if level == 1:
        k = random.choice([k for k in (1, 2, 3) if target - k >= 1])
        names = _elements(sorted(random.sample(range(1, min(target, 9) + 1), k)))
        m = target - k
        q = (
            f"집합 {univ}의 부분집합 중에서 {names} 반드시 원소로 갖는 것의 개수가 ${_pow2(m)}$일 때, "
            "자연수 $n$의 값을 구하시오."
        )
        exp = (
            f"{names} 반드시 원소로 가지므로 나머지 $n-{k}$개의 원소로 부분집합을 만들면 됩니다.\n\n"
            f"$2^{{n-{k}}}={_pow2(m)}$이므로 $n-{k}={m}$, 즉 $n={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        # 1, ..., a 는 반드시 포함하고, 그 다음 b 개는 포함하지 않는다. 남은 n-a-b 개로 만든다.
        # (target 이 2 면 남는 원소가 없어 둘 다 정할 수 없으므로 포함 조건 없이 제외만 한다.)
        combos = [(a, b) for a in (1, 2) for b in (1, 2) if target - a - b >= 1] or [(0, 1)]
        a, b = random.choice(combos)
        m = target - a - b
        picked = random.sample(range(1, min(target, 9) + 1), a + b)
        inc = _elements(sorted(picked[:a])) if a else ""
        exc = _elements(sorted(picked[a:]))
        cond = f"{inc} 반드시 원소로 갖고 {exc} 원소로 갖지 않는" if a else f"{exc} 원소로 갖지 않는"
        q = (
            f"집합 {univ}의 부분집합 중에서 {cond} 것의 개수가 "
            f"${_pow2(m)}$일 때, 자연수 $n$의 값을 구하시오."
        )
        exp = (
            f"원소 {a + b}개는 포함 여부가 정해져 있으므로 나머지 $n-{a + b}$개의 원소로 부분집합을 만들면 됩니다.\n\n"
            f"$2^{{n-{a + b}}}={_pow2(m)}$이므로 $n-{a + b}={m}$, 즉 $n={target}$"
        )
        return q, exp, str(target)
    # 상: 원소의 개수가 홀수(짝수)인 부분집합은 전체의 절반, 특정 원소를 반드시 포함하면 그 절반이다.
    parity = random.choice(["홀수", "짝수"])
    with_one = target >= 3 and random.random() < 0.5
    if with_one:
        m = target - 2
        q = (
            f"집합 {univ}의 부분집합 중에서 $1$을 반드시 원소로 갖고 원소의 개수가 {parity}인 것의 개수가 "
            f"${_pow2(m)}$일 때, 자연수 $n$의 값을 구하시오."
        )
        exp = (
            "원소의 개수가 $k$인 집합의 부분집합 중 원소의 개수가 홀수인 것과 짝수인 것은 각각 $2^{k-1}$개입니다.\n\n"
            "$1$을 반드시 원소로 가지므로 나머지 $n-1$개의 원소로 만드는 부분집합 중 "
            f"원소의 개수가 {'짝수' if parity == '홀수' else '홀수'}인 것을 세면 되고, 그 개수는 $2^{{n-2}}$입니다.\n\n"
            f"$2^{{n-2}}={_pow2(m)}$이므로 $n-2={m}$, 즉 $n={target}$"
        )
        return q, exp, str(target)
    m = target - 1
    q = (
        f"집합 {univ}의 부분집합 중에서 원소의 개수가 {parity}인 것의 개수가 ${_pow2(m)}$일 때, "
        "자연수 $n$의 값을 구하시오."
    )
    exp = (
        "원소의 개수가 $n$인 집합의 부분집합 중 원소의 개수가 홀수인 것과 짝수인 것은 각각 $2^{n-1}$개입니다.\n\n"
        f"$2^{{n-1}}={_pow2(m)}$이므로 $n-1={m}$, 즉 $n={target}$"
    )
    return q, exp, str(target)


def h_prop(target, level):
    """충분조건과 필요조건 (하: 일차부등식, 중: 절댓값 부등식, 상: 이차부등식과 절댓값 부등식)."""
    if level == 1:
        # 답이 문제에 그대로 보이지 않도록, 일차부등식을 한 번 풀어야 x ≤ target 이 나오게 한다.
        a, b = random.choice([2, 3, 4, 5]), _nonzero(-9, 9)
        c = a * target + b
        q = (
            f"두 조건 $p: {_lin(a, b)}\\le {c}$, $q: x\\le k$에 대하여 $p$가 $q$이기 위한 충분조건이 되도록 하는 "
            "실수 $k$의 최솟값을 구하시오."
        )
        exp = (
            f"$p$의 진리집합은 ${_lin(a, b)}\\le {c}$에서 ${a}x\\le {c - b}$, 즉 $x\\le {target}$입니다.\n\n"
            f"$p$가 $q$이기 위한 충분조건이므로 $p$의 진리집합이 $q$의 진리집합에 포함되어야 합니다.\n\n"
            f"$\\{{x\\mid x\\le {target}\\}}\\subset\\{{x\\mid x\\le k\\}}$에서 $k\\ge {target}$이므로 최솟값은 ${target}$"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            c = random.randint(-4, 8)
            d = target - c
            if d > 0 and c != 0:
                break
        q = (
            f"두 조건 $p: |{_lin(1, -c)}|\\le {d}$, $q: x\\le k$에 대하여 $p$가 $q$이기 위한 충분조건이 되도록 하는 "
            "실수 $k$의 최솟값을 구하시오."
        )
        exp = (
            f"$p$의 진리집합은 ${c - d}\\le x\\le {c + d}$이고, $q$의 진리집합은 $x\\le k$입니다.\n\n"
            f"$p$의 진리집합이 $q$의 진리집합에 포함되려면 $k\\ge {c + d}$이므로 최솟값은 ${target}$"
        )
        return q, exp, str(target)
    # 한쪽 끝에서 c 까지의 거리가 target 이고 다른 쪽 끝은 그보다 가깝게(또는 같게) 잡는다.
    while True:
        a = random.randint(-12, 0)
        near = random.randint(max(1, target // 2), target)
        if random.random() < 0.5:
            c, b = a + target, a + target + near      # (c - a) 가 가장 멀다
        else:
            c, b = a + near, a + near + target        # (b - c) 가 가장 멀다
        if max(c - a, b - c) == target and b != 0 and a != 0:
            break
    q = (
        f"두 조건 $p: {_fprod(_lin(1, -a), _lin(1, -b))}\\le 0$, $q: |{_lin(1, -c)}|\\le m$에 대하여 "
        "$p$가 $q$이기 위한 충분조건이 되도록 하는 양수 $m$의 최솟값을 구하시오."
    )
    exp = (
        f"$p$의 진리집합은 ${a}\\le x\\le {b}$이고, $q$의 진리집합은 ${c}-m\\le x\\le {c}+m$입니다.\n\n"
        f"${c}-m\\le {a}$에서 $m\\ge {c - a}$, ${b}\\le {c}+m$에서 $m\\ge {b - c}$이므로 "
        f"$m\\ge {target}$이고 최솟값은 ${target}$"
    )
    return q, exp, str(target)


# ---------- 함수와 그래프 ----------
def h_composite(target, level):
    """합성함수 (하: 일차함수의 합성, 중: 이차함수와 일차함수의 합성, 상: (f∘f)(x) 가 주어졌을 때)."""
    if level == 1:
        while True:
            # x 의 계수가 1 이면 단순한 덧셈이 되어 너무 쉬우므로, 두 함수 모두 계수는 1 이 아닌 값으로 잡는다.
            a, b = random.choice([-4, -3, -2, 2, 3, 4]), random.randint(-6, 6)
            c, k = random.choice([-4, -3, -2, 2, 3, 4]), random.randint(-3, 4)
            d = target - c * (a * k + b)
            if abs(d) <= 12:
                break
        v = a * k + b
        q = (
            f"두 함수 $f(x)={_lin(a, b)}$, $g(x)={_lin(c, d)}$에 대하여 $(g\\circ f)({k})$의 값을 구하시오."
        )
        exp = (
            f"$f({k})={a}\\times{_p(k)} {_sgn(b)}={v}$이므로\n\n"
            f"$(g\\circ f)({k})=g(f({k}))=g({v})={c}\\times{_p(v)} {_sgn(d)}={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            c, d, k = _nonzero(-3, 3), random.randint(-5, 5), random.randint(-2, 3)
            a, v = _nonzero(-4, 4), c * k + d
            b = target - v * v - a * v
            if abs(b) <= 20:
                break
        q = (
            f"두 함수 $f(x)=x^{{2}} {_tx(a)} {_sgn(b)}$, $g(x)={_lin(c, d)}$에 대하여 "
            f"$(f\\circ g)({k})$의 값을 구하시오."
        )
        exp = (
            f"$g({k})={c}\\times{_p(k)} {_sgn(d)}={v}$이므로\n\n"
            f"$(f\\circ g)({k})=f({v})="
            + _j(f"{_p(v)}^{{2}}", f"{'+' if a > 0 else '-'} {abs(a)}\\times{_p(v)}", _sgn(b))
            + f"={target}$"
        )
        return q, exp, str(target)
    while True:
        a, k = random.choice([2, 3]), random.randint(1, 5)
        b = target - a * k
        if b != 0 and abs(b) <= 20:
            break
    px, qx = a * a, (a + 1) * b
    q = (
        f"함수 $f(x)=ax+b$ ($a>0$)에 대하여 $(f\\circ f)(x)={px}x {_sgn(qx)}$일 때, $f({k})$의 값을 구하시오. "
        "(단, $a$, $b$는 상수이다.)"
    )
    exp = (
        f"$(f\\circ f)(x)=a(ax+b)+b=a^{{2}}x+(a+1)b$이므로\n\n"
        f"$a^{{2}}={px}$, $(a+1)b={qx}$입니다. $a>0$이므로 $a={a}$, $b={b}$입니다.\n\n"
        f"따라서 $f({k})={a}\\times{k} {_sgn(b)}={target}$"
    )
    return q, exp, str(target)


def h_inverse(target, level):
    """역함수 (하: 일차함수의 역함수의 값, 중: x≥0 에서 이차함수의 역함수의 값, 상: 합성함수의 역함수)."""
    if level == 1:
        while True:
            a, b = _nonzero(-4, 5), random.randint(-8, 8)
            if a == 1:
                continue
            k = a * target + b
            if abs(k) <= 80:
                break
        q = (
            f"함수 $f(x)={_lin(a, b)}$의 역함수를 $g(x)$라 할 때, $g({k})$의 값을 구하시오."
        )
        exp = (
            f"$g({k})=m$이라 하면 $f(m)={k}$이므로\n\n$${_lin(a, b, 'm')}={k}$$\n\n따라서 $m={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        c = random.randint(-6, 6)
        k = target * target + c
        q = (
            f"$x\\ge 0$에서 정의된 함수 $f(x)=x^{{2}} {_sgn(c)}$의 역함수를 $g(x)$라 할 때, $g({k})$의 값을 구하시오."
        )
        exp = (
            f"$g({k})=m$이라 하면 $f(m)={k}$ ($m\\ge 0$)이므로\n\n"
            f"$$m^{{2}} {_sgn(c)}={k}$$\n\n$m^{{2}}={target ** 2}$이고 $m\\ge 0$이므로 $m={target}$"
        )
        return q, exp, str(target)
    while True:
        a, b, k = _nonzero(-4, 5), random.randint(-8, 8), random.randint(2, 7)
        if a == 1:
            continue
        # f^{-1}(g(k)) = target  <=>  g(k) = f(target)  <=>  k^2 - 1 = a*target + b
        b = k * k - 1 - a * target
        if abs(b) <= 40:
            break
    q = (
        f"두 함수 $f(x)={_lin(a, b)}$, $g(x)=x^{{2}}-1$에 대하여 $(f^{{-1}}\\circ g)({k})$의 값을 구하시오."
    )
    exp = (
        f"$g({k})={k}^{{2}}-1={k * k - 1}$이므로 $(f^{{-1}}\\circ g)({k})=f^{{-1}}({k * k - 1})$입니다.\n\n"
        f"$f^{{-1}}({k * k - 1})=m$이라 하면 $f(m)={k * k - 1}$이므로 ${_lin(a, b, 'm')}={k * k - 1}$, 즉 $m={target}$"
    )
    return q, exp, str(target)


def h_rational(target, level):
    """유리함수 (하: 그래프가 지나는 점에서 상수, 중: 점근선의 교점, 상: 점근선과 한 점으로 세 수의 합)."""
    if level == 1:
        while True:
            a, b = random.randint(-4, 4), random.randint(-4, 4)
            dv = random.choice([d for d in range(-target, target + 1) if d and target % d == 0])
            p = a + dv
            qv = b + target // dv
            if abs(p) <= 9 and abs(qv) <= 12 and a != 0:
                break
        q = (
            f"함수 $y=\\frac{{k}}{{{_lin(1, -a)}}} {_sgn(b)}$의 그래프가 점 $({p}, {qv})$를 지날 때, "
            "상수 $k$의 값을 구하시오."
        )
        dv_ = p - a                                   # 분모가 ±1 이면 분수로 쓰지 않는다.
        if dv_ == 1:
            tail = f"$k={target}$"
        else:
            lhs = "-k" if dv_ == -1 else f"\\frac{{k}}{{{dv_}}}"
            tail = f"${lhs}={qv - b}$이므로 $k={target}$"
        exp = (
            f"$x={p}$, $y={qv}$를 대입하면\n\n$${qv}=\\frac{{k}}{{{p} {_sgn(-a)}}} {_sgn(b)}$$\n\n"
            + tail
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            a, b = random.randint(-6, 9), random.randint(-6, 9)
            if a + b == target and a != 0 and b != 0:
                c = random.randint(-9, 9)
                if c + a * b != 0 and c != 0:
                    break
        q = (
            f"함수 $y=\\frac{{{_lin(b, c)}}}{{{_lin(1, -a)}}}$의 그래프의 두 점근선의 교점의 좌표를 $(p, q)$라 할 때, "
            "$p+q$의 값을 구하시오."
        )
        rest = c + a * b
        exp = (
            f"$y=\\frac{{{_lin(b, c)}}}{{{_lin(1, -a)}}}={b} {'+' if rest > 0 else '-'} "
            f"\\frac{{{abs(rest)}}}{{{_lin(1, -a)}}}$이므로\n\n"
            f"점근선은 $x={a}$, $y={b}$이고 교점은 $({a}, {b})$입니다.\n\n"
            f"따라서 $p+q={a}+{_p(b)}={target}$"
        )
        return q, exp, str(target)

    def make():
        m, n = random.randint(-5, 6), random.randint(-4, 6)
        p, qv = random.randint(-6, 8), random.randint(-6, 8)
        # p == m 이면 점이 점근선 x=m 위에 있고, qv == n 이면 점이 점근선 y=n 위에 있다.
        # (후자이면 b = -nm 이 되어 분자·분모가 약분되므로 함수가 상수가 된다. 두 경우 모두 문제가 성립하지 않는다.)
        if p == m or qv == n:
            return None
        return m, n, p, qv

    def solve(s):
        m, n, p, qv = s
        num = qv * (p - m) - n * p       # b
        return n + num - m               # a + b + c  (a=n, c=-m)

    m, n, p, qv = _roll(make, solve, target)
    b = qv * (p - m) - n * p
    c = -m
    q = (
        f"함수 $y=\\frac{{ax+b}}{{x+c}}$의 그래프의 점근선의 방정식이 $x={m}$, $y={n}$이고 점 $({p}, {qv})$를 "
        "지날 때, 상수 $a$, $b$, $c$에 대하여 $a+b+c$의 값을 구하시오."
    )
    exp = (
        f"점근선이 $x={m}$, $y={n}$이므로 $c={c}$, $a={n}$입니다.\n\n"
        f"점 $({p}, {qv})$를 지나므로 ${qv}=\\frac{{{n}\\times{_p(p)}+b}}{{{p} {_sgn(c)}}}$에서 $b={b}$입니다.\n\n"
        f"따라서 $a+b+c={n}+{_p(b)}+{_p(c)}={target}$"
    )
    return q, exp, str(target)


# ==========================================
# [고2] 수학Ⅰ·수학Ⅱ
# ==========================================
# ---------- 지수함수와 로그함수 ----------
def s_log(target, level):
    """로그 (하: 로그의 합, 중: 밑의 변환, 상: 로그방정식)."""
    if level == 1:
        # 진수가 너무 커지지 않도록(64 이하·81 이하) 로그를 2~4개로 나눠서 합이 target 이 되게 한다.
        B, cap = random.choice([(2, 6), (3, 4)])
        lo = max(2, -(-target // cap))                       # 항 하나의 최댓값이 cap 이므로 필요한 최소 항의 수
        n_terms = random.randint(lo, min(4, target))
        while True:
            cuts = sorted(random.sample(range(1, target), n_terms - 1)) if n_terms > 1 else []
            parts = [b - a for a, b in zip([0] + cuts, cuts + [target])]
            if all(1 <= e <= cap for e in parts):
                break
        random.shuffle(parts)
        args = [B ** e for e in parts]
        q = "$" + "+".join(f"\\log_{{{B}}} {v}" for v in args) + "$의 값을 구하시오."
        prod = "\\times".join(map(str, args))
        exp = (
            "로그의 성질 $\\log_{a}x+\\log_{a}y=\\log_{a}xy$에 의하여\n\n"
            f"$$\\log_{{{B}}}({prod})=\\log_{{{B}}}{B}^{{{target}}}={target}$$"
        )
        return q, exp, str(target)
    if level == 2:
        if random.random() < 0.5:
            B, k = random.choice([2, 3]), random.choice([2, 3])
            m = target * k
            q = f"$\\log_{{{B ** k}}} {B}^{{{m}}}$의 값을 구하시오."
            exp = (
                f"$\\log_{{{B ** k}}}{B}^{{{m}}}=\\log_{{{B}^{{{k}}}}}{B}^{{{m}}}=\\frac{{{m}}}{{{k}}}\\log_{{{B}}}{B}={target}$"
            )
            return q, exp, str(target)
        a, b, c = random.sample([2, 3, 5, 7], 3)
        q = f"$\\log_{{{a}}} {b}\\times\\log_{{{b}}} {c}\\times\\log_{{{c}}} {a}^{{{target}}}$의 값을 구하시오."
        exp = (
            "밑의 변환 공식 $\\log_{a}b\\times\\log_{b}c=\\log_{a}c$를 이용하면\n\n"
            f"$$\\log_{{{a}}}{b}\\times\\log_{{{b}}}{c}\\times\\log_{{{c}}}{a}^{{{target}}}"
            f"=\\log_{{{a}}}{a}^{{{target}}}={target}$$"
        )
        return q, exp, str(target)

    def make():
        return random.choice([2, 3, 5]), random.randint(2, 4), random.randint(-8, 10), random.randint(-3, 14)

    def solve(s):
        b, m, p, qv = s
        if p == -qv:                                  # 두 진수가 같은 식이면 로그 두 개를 따로 두는 의미가 없다.
            return None
        bound = max(qv, -p)                           # 진수의 조건: x + p > 0 이고 x - qv > 0
        if target <= bound or (target + p) * (target - qv) != b ** m:
            return None
        other = (qv - p) - target                     # 이차방정식의 다른 한 근
        if other > bound:                             # 다른 근도 진수 조건을 만족하면 해가 둘이 된다.
            return None
        return target

    b, m, p, qv = _roll(make, solve, target)
    other = (qv - p) - target
    bound = max(qv, -p)
    u, v = _lin(1, p), _lin(1, -qv)
    arg = lambda e: e if e == "x" else f"({e})"     # 진수가 x 하나뿐이면 괄호를 쓰지 않는다.
    gap = lambda e: " " if e == "x" else ""          # \log_{3} x 처럼 괄호가 없을 때만 한 칸 띄운다.
    q = (
        f"방정식 $\\log_{{{b}}}{gap(u)}{arg(u)}+\\log_{{{b}}}{gap(v)}{arg(v)}={m}$의 해를 구하시오."
    )
    exp = (
        f"진수의 조건에 의하여 $x>{bound}$입니다.\n\n"
        f"$\\log_{{{b}}}{gap(u)}{arg(u)}{arg(v)}={m}$이므로 ${arg(u)}{arg(v)}={b ** m}$입니다.\n\n"
        f"정리하면 ${_fprod(_lin(1, -target), _lin(1, -other))}=0$이므로 $x={target}$ 또는 $x={other}$이고, "
        f"진수의 조건을 만족시키는 해는 $x={target}$"
    )
    return q, exp, str(target)


def s_exp(target, level):
    """지수 (하: 밑이 같은 지수방정식, 중: 밑이 2의 거듭제곱으로 서로 다른 방정식, 상: 치환하여 푸는 이차꼴의 두 근의 합)."""
    if level == 1:
        while True:
            m, n = random.randint(1, 9), random.randint(1, 9)
            p = target - m - n
            if abs(p) <= 9:
                break
        pw = lambda e: "2" if e == 1 else f"2^{{{e}}}"
        q = f"방정식 $2^{{{_lin(1, -p)}}}={pw(m)}\\times {pw(n)}$의 해를 구하시오."
        exp = (
            f"${pw(m)}\\times {pw(n)}=2^{{{m + n}}}$이므로 ${_lin(1, -p)}={m + n}$\n\n따라서 $x={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        # (2^α)^(x-p) = (2^β)^s  =>  α(x-p) = βs.  좌변·우변의 밑을 2 의 거듭제곱으로 서로 다르게 준다.
        while True:
            al, be = random.choice([(2, 3), (3, 2), (2, 4), (4, 2), (3, 4), (4, 3)])
            p = _nonzero(-4, 6)
            if (al * (target - p)) % be:
                continue
            s = al * (target - p) // be
            if 1 <= s <= 30:
                break
        base = lambda e: str(2 ** e)
        q = f"방정식 ${base(al)}^{{{_lin(1, -p)}}}={base(be)}^{{{s}}}$의 해를 구하시오."
        exp = (
            f"${base(al)}=2^{{{al}}}$, ${base(be)}=2^{{{be}}}$이므로 양변을 밑이 $2$인 거듭제곱으로 고치면\n\n"
            f"$$2^{{{al}{_fac(_lin(1, -p))}}}=2^{{{be}\\times {s}}}$$\n\n"
            f"${al}{_fac(_lin(1, -p))}={be * s}$이므로 $x={target}$"
        )
        return q, exp, str(target)
    # 상: 2^(x-a) = t 로 치환하면 t^2 - (2^i + 2^j) t + 2^(i+j) = 0.  두 근은 x = a+i, a+j 이므로
    # 두 근의 합 = 2a + i + j = target.  (큰 거듭제곱이 나오지 않도록 평행이동 a 를 쓴다.)
    while True:
        i, j = sorted(random.sample(range(0, 5), 2))
        if (target - i - j) >= 0 and (target - i - j) % 2 == 0:
            a = (target - i - j) // 2
            break
    S, P = 2 ** i + 2 ** j, 2 ** (i + j)
    shift = "x" if a == 0 else f"x-{a}"
    q = (
        f"방정식 $4^{{{shift}}}-{S}\\times 2^{{{shift}}}+{P}=0$의 두 실근을 $\\alpha$, $\\beta$라 할 때, "
        "$\\alpha+\\beta$의 값을 구하시오."
    )
    exp = (
        f"$2^{{{shift}}}=t$ ($t>0$)로 놓으면 $t^{{2}}-{S}t+{P}=0$입니다.\n\n"
        f"$(t-{2 ** i})(t-{2 ** j})=0$이므로 $2^{{{shift}}}={2 ** i}$ 또는 $2^{{{shift}}}={2 ** j}$이고, "
        f"$\\alpha={a + i}$, $\\beta={a + j}$입니다.\n\n"
        f"따라서 $\\alpha+\\beta={target}$"
    )
    return q, exp, str(target)


# ---------- 삼각함수 ----------
def s_trig(target, level):
    """삼각함수 (하: 특수각의 값, 중: 그래프의 최대·최소·주기, 상: 좁은 구간에서 방정식의 근의 개수)."""
    if level == 1:
        vals = [
            (r"\sin\frac{\pi}{6}", Fraction(1, 2)),
            (r"\cos\frac{\pi}{3}", Fraction(1, 2)),
            (r"\sin\frac{\pi}{2}", Fraction(1)),
            (r"\cos\pi", Fraction(-1)),
            (r"\tan\frac{\pi}{4}", Fraction(1)),
            (r"\cos 0", Fraction(1)),
            (r"\sin\frac{3\pi}{2}", Fraction(-1)),
        ]

        def make():
            (t1, v1), (t2, v2) = random.sample(vals, 2)
            a = random.randint(1, 20)
            need = Fraction(target) - a * v1
            b = need / v2
            if b.denominator != 1 or b == 0 or abs(b) > 30:
                return None
            return t1, v1, t2, v2, a, int(b)

        t1, v1, t2, v2, a, b = _roll(make, lambda s: s[4] * s[1] + s[5] * s[3], target)
        ca = "" if a == 1 else str(a)
        cb = "" if abs(b) == 1 else str(abs(b))
        sign = "+" if b > 0 else "-"
        q = f"${ca}{t1} {sign} {cb}{t2}$의 값을 구하시오."
        f1, f2 = _frac(v1.numerator, v1.denominator), _frac(v2.numerator, v2.denominator)
        exp = (
            f"${t1}={f1}$, ${t2}={f2}$이므로\n\n"
            f"$${a}\\times({f1}) {sign} {abs(b)}\\times({f2})={target}$$"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            a, bq, c = random.randint(1, 8), random.choice([2, 3, 4]), random.randint(-6, 6)
            if a + bq + c == target:
                break
        per = {2: r"\pi", 3: r"\frac{2\pi}{3}", 4: r"\frac{\pi}{2}"}[bq]
        q = (
            f"함수 $y=a\\sin bx+c$ ($a>0$, $b>0$)의 최댓값이 ${a + c}$, 최솟값이 ${c - a}$이고 주기가 ${per}$일 때, "
            "상수 $a$, $b$, $c$에 대하여 $a+b+c$의 값을 구하시오."
        )
        exp = (
            f"최댓값은 $a+c={a + c}$, 최솟값은 $-a+c={c - a}$이므로 $a={a}$, $c={c}$입니다.\n\n"
            f"주기는 $\\frac{{2\\pi}}{{b}}={per}$이므로 $b={bq}$입니다.\n\n"
            f"따라서 $a+b+c={target}$"
        )
        return q, exp, str(target)
    # 구간 [0, Lπ) 에서 sin kx, cos kx 가 1 또는 -1 이 되는 x 의 개수가 target 이 되도록 k 를 고른다.
    # t = kx 는 [0, kLπ) 를 움직이고, 해는 t = t0·π + 2nπ (n = 0, 1, ...) 이므로 반 주기가 남는 경우까지 따져야 한다.
    kind = random.choice(["sin", "cos"])
    rv = random.choice([1, -1])
    big_l = random.choice([1, 2])
    t0 = {("cos", 1): Fraction(0), ("cos", -1): Fraction(1), ("sin", 1): Fraction(1, 2), ("sin", -1): Fraction(3, 2)}[(kind, rv)]
    ks = [k for k in range(2, 40) if math.ceil((k * big_l - t0) / 2) == target]
    k = random.choice(ks)
    top = k * big_l
    big_l_tex = r"\pi" if big_l == 1 else r"2\pi"
    q = f"$0\\le x<{big_l_tex}$에서 방정식 $\\{kind} {k}x={rv}$의 서로 다른 실근의 개수를 구하시오."
    sol = {
        ("cos", 1): r"2n\pi", ("cos", -1): r"(2n+1)\pi",
        ("sin", 1): r"\frac{\pi}{2}+2n\pi", ("sin", -1): r"\frac{3\pi}{2}+2n\pi",
    }[(kind, rv)]
    ns_ = "$n=0, 1$" if target == 2 else f"$n=0, 1, \\cdots, {target - 1}$"
    exp = (
        f"$\\{kind} t={rv}$을 만족시키는 $t$는 $t={sol}$ ($n$은 정수)입니다.\n\n"
        f"$t={k}x$이고 $0\\le x<{big_l_tex}$이므로 $0\\le t<{top}\\pi$입니다.\n\n"
        f"이 범위에서 $t={sol}$인 $t$의 개수를 세면 {ns_}이므로 모두 ${target}$개"
    )
    return q, exp, str(target)


# ---------- 수열 ----------
def s_seq(target, level):
    """수열 (하: 첫째항·공차로 항의 번호, 중: 두 항으로 일반항을 구해 항의 번호, 상: 시그마)."""
    if level == 1:
        a1, d = random.randint(1, 8), random.choice([2, 3, 4, 5, 6, 7])
        N = a1 + (target - 1) * d
        q = f"첫째항이 ${a1}$, 공차가 ${d}$인 등차수열 $\\{{a_n\\}}$에서 $a_n={N}$일 때, 자연수 $n$의 값을 구하시오."
        exp = (
            f"일반항은 $a_n={a1}+(n-1)\\times{d}$입니다.\n\n"
            f"$${a1}+(n-1)\\times{d}={N}$$\n\n$(n-1)\\times{d}={N - a1}$이므로 $n={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        # 두 항 a_p, a_q 가 주어지면 공차와 첫째항을 먼저 구한 다음 a_n = N 이 되는 n 을 구한다. (두 단계)
        while True:
            a1, d = random.randint(-5, 8), random.choice([2, 3, 4, 5])
            p, r = sorted(random.sample([k for k in range(2, 9) if k != target], 2))
            N = a1 + (target - 1) * d
            if abs(N) <= 80:
                break
        ap, ar = a1 + (p - 1) * d, a1 + (r - 1) * d
        q = (
            f"등차수열 $\\{{a_n\\}}$에서 $a_{{{p}}}={ap}$, $a_{{{r}}}={ar}$일 때, "
            f"$a_n={N}$인 자연수 $n$의 값을 구하시오."
        )
        exp = (
            f"공차는 $d=\\frac{{{ar}-{_p(ap)}}}{{{r}-{p}}}={d}$입니다.\n\n"
            f"$a_{{{p}}}=a_{{1}}+{p - 1}\\times{d}={ap}$이므로 $a_{{1}}={a1}$이고 "
            f"일반항은 $a_{{n}}={a1}+(n-1)\\times{d}$입니다.\n\n"
            f"$a_{{n}}={N}$에서 $(n-1)\\times{d}={N - a1}$이므로 $n={target}$"
        )
        return q, exp, str(target)
    a, b = random.choice([2, 3]), random.randint(-3, 6)       # a 가 크면 N 이 수백이 되어 계산이 지나치게 길다.
    N = a * target * (target + 1) // 2 + b * target
    q = (
        f"$\\displaystyle\\sum_{{k=1}}^{{n}}({_lin(a, b, 'k')})={N}$일 때, 자연수 $n$의 값을 구하시오."
    )
    body = _j(f"{a}\\times\\frac{{n(n+1)}}{{2}}", _tx(b, "n"))
    c = a * (target + 1) + 2 * b                      # a·n² + (a+2b)·n - 2N = (n - target)(a·n + c)
    exp = (
        f"$\\sum_{{k=1}}^{{n}}k=\\frac{{n(n+1)}}{{2}}$이므로\n\n"
        f"$$\\sum_{{k=1}}^{{n}}({_lin(a, b, 'k')})={body}={N}$$\n\n"
        f"양변에 $2$를 곱하여 정리하면 ${_poly([a, a + 2 * b, -2 * N], 'n')}=0$이고 "
        f"$(n-{target})({_lin(a, c, 'n')})=0$입니다.\n\n"
        f"$n$은 자연수이므로 $n={target}$"
    )
    return q, exp, str(target)


# ---------- 함수의 극한과 연속 ----------
def s_limit(target, level):
    """극한과 연속 (하: 0/0 꼴의 극한값, 중: 극한값으로 두 상수의 합, 상: 연속이 되도록 두 상수의 합)."""
    if level == 1:
        while True:
            a = _nonzero(-5, 6)          # x→0 이면 그냥 x 로 약분하는 꼴이라 너무 쉽다.
            p = target - 2 * a
            if abs(p) <= 15:
                break
        qc = -a * a - p * a
        num = _poly([1, p, qc])
        q = (
            "다음 극한값을 구하시오.\n\n"
            f"$$\\lim_{{x\\to {a}}}\\frac{{{num}}}{{{_lin(1, -a)}}}$$"
        )
        exp = (
            f"분자를 인수분해하면 ${num}={_fprod(_lin(1, -a), _lin(1, a + p))}$이므로\n\n"
            f"$$\\lim_{{x\\to {a}}}{_fac(_lin(1, a + p))}={_j(str(a), _sgn(a + p))}={target}$$"
        )
        return q, exp, str(target)
    if level == 2:
        def make():
            return random.randint(-5, 6), random.randint(-8, 12)

        def solve(s):
            a, c = s
            p = c - 2 * a
            return p + (-a * a - p * a)

        a, c = _roll(make, solve, target)
        p = c - 2 * a
        qc = -a * a - p * a
        q = (
            f"$\\displaystyle\\lim_{{x\\to {a}}}\\frac{{x^{{2}}+px+q}}{{{_lin(1, -a)}}}={c}$일 때, "
            "상수 $p$, $q$에 대하여 $p+q$의 값을 구하시오."
        )
        m = c - a
        exp = (
            f"$x\\to {a}$일 때 (분모) $\\to 0$이고 극한값이 존재하므로 (분자) $\\to 0$이어야 합니다.\n\n"
            f"따라서 분자는 ${_fprod(_lin(1, -a), _lin(1, m, 'x'))}$ 꼴이어야 하고, "
            f"극한값은 ${_j(str(a), _sgn(m))}={c}$입니다.\n\n"
            f"${_fprod(_lin(1, -a), _lin(1, m))}$을 전개하면 ${_poly([1, m - a, -a * m])}$이므로 "
            f"$p={m - a}$, $q={-a * m}$이고 $p+q={target}$"
        )
        return q, exp, str(target)

    def make3():
        return random.randint(-12, 12), random.randint(-6, 6)

    def solve3(s):
        a, c = s
        b = -c * c - a * c
        k = 2 * c + a
        return b + k

    a, c = _roll(make3, solve3, target)
    b = -c * c - a * c
    k = 2 * c + a
    top = _j("x^{2}", _tx(a), "+ b")
    q = (
        "함수\n\n"
        + rf"$$f(x)=\begin{{cases}}\dfrac{{{top}}}{{{_lin(1, -c)}}}&(x\ne {c})\\k&(x={c})\end{{cases}}$$"
        + f"\n\n가 $x={c}$에서 연속일 때, 상수 $b$, $k$에 대하여 $b+k$의 값을 구하시오."
    )
    zero = _j(str(c * c), _sgn(a * c), "+ b")
    exp = (
        f"$x={c}$에서 연속이려면 $\\lim_{{x\\to {c}}}f(x)=f({c})=k$이어야 합니다.\n\n"
        f"$x\\to {c}$일 때 (분모) $\\to 0$이므로 (분자) $\\to 0$이어야 하고, ${zero}=0$에서 $b={b}$입니다.\n\n"
        f"분자는 ${_poly([1, a, b])}={_fprod(_lin(1, -c), _lin(1, c + a))}$이므로 "
        f"$k=\\lim_{{x\\to {c}}}{_fac(_lin(1, c + a))}={k}$입니다.\n\n"
        f"따라서 $b+k={_p(b)}+{_p(k)}={target}$"
    )
    return q, exp, str(target)


# ---------- 미분 ----------
def s_deriv(target, level):
    """미분 (하: 미분계수, 중: 미분계수의 정의를 이용한 극한값, 상: 극값으로 상수)."""
    if level == 1:
        while True:
            p, a = random.randint(-6, 6), random.randint(-3, 4)
            qq = target - 3 * a * a - 2 * p * a
            r = random.randint(-6, 6)
            if qq != 0 and abs(qq) <= 25:
                break
        f = _poly([1, p, qq, r])
        q = f"함수 $f(x)={f}$에 대하여 $f'({a})$의 값을 구하시오."
        if a == 0:
            calc = f"f'(0)={qq}"
        else:
            calc = f"f'({a})=" + _j(f"3\\times{_p(a)}^{{2}}", _sgn(2 * p * a), _sgn(qq)) + f"={target}"
        exp = (
            f"$f'(x)={_poly([3, 2 * p, qq])}$이므로\n\n$${calc}$$"
        )
        return q, exp, str(target)
    if level == 2:
        # 미분계수의 정의: lim_{h→0} {f(a+mh) - f(a-nh)}/h = (m+n) f'(a).
        # (m+n) 이 target 의 약수라서 f'(a) = target/(m+n) 이 정수이고, 큰 계수가 나오지 않는다.
        divs = [d for d in (2, 3, 4, 5) if target % d == 0]
        while True:
            d = random.choice(divs)
            m = random.randint(1, d - 1)
            n = d - m
            a, p, r = random.randint(-3, 4), random.randint(-6, 6), random.randint(-6, 6)
            F = target // d
            qq = F - 3 * a * a - 2 * p * a
            if qq != 0 and abs(qq) <= 25:
                break
        f = _poly([1, p, qq, r])
        lo = _lin(m, a, "h")
        hi = _lin(-n, a, "h")
        calc = str(qq) if a == 0 else _j(f"3\\times{_p(a)}^{{2}}", _sgn(2 * p * a), _sgn(qq))
        cm, cn = ("" if m == 1 else str(m)), ("" if n == 1 else str(n))
        q = (
            f"함수 $f(x)={f}$에 대하여 $\\displaystyle\\lim_{{h\\to 0}}\\frac{{f({lo})-f({hi})}}{{h}}$의 값을 구하시오."
        )
        exp = (
            f"$$\\lim_{{h\\to 0}}\\frac{{f({lo})-f({hi})}}{{h}}={cm}f'({a})+{cn}f'({a})={d}f'({a})$$\n\n"
            f"$f'(x)={_poly([3, 2 * p, qq])}$이므로 $f'({a})={calc}={F}$\n\n"
            f"따라서 구하는 값은 ${d}\\times{F}={target}$"
        )
        return q, exp, str(target)
    while True:
        al, be = random.randint(-3, 1), random.randint(2, 5)
        if (al + be) % 2 != 0:
            continue
        A, B = -3 * (al + be) // 2, 3 * al * be
        M = al ** 3 + A * al * al + B * al + target
        if B != 0 and A != 0 and abs(M) <= 80:
            break
    f = _j("x^{3}", _tx(A, "x^{2}"), _tx(B), "+ k")
    q = f"삼차함수 $f(x)={f}$의 극댓값이 ${M}$일 때, 상수 $k$의 값을 구하시오."
    exp = (
        f"$f'(x)={_poly([3, 2 * A, B])}=3{_fprod(_lin(1, -al), _lin(1, -be))}$이므로 "
        f"$x={al}$에서 극대, $x={be}$에서 극소입니다.\n\n"
        f"극댓값은 $f({al})={al ** 3 + A * al * al + B * al}+k={M}$이므로 $k={target}$"
    )
    return q, exp, str(target)


# ---------- 적분 ----------
def s_integral(target, level):
    """적분 (하: 정적분의 값, 중: 정적분 조건으로 상수, 상: 넓이로 상수)."""
    if level == 1:
        def make():
            return random.choice([3, 6, 9]), random.choice([-6, -4, -2, 2, 4, 6, 8]), random.randint(-6, 8), random.randint(1, 3)

        def solve(s):
            p, qq, r, a = s
            return p * a ** 3 // 3 + qq * a * a // 2 + r * a

        p, qq, r, a = _roll(make, solve, target)
        f = _poly([p, qq, r])
        q = f"정적분 $\\displaystyle\\int_{{0}}^{{{a}}}({f})\\,dx$의 값을 구하시오."
        anti = _poly([p // 3, qq // 2, r, 0])
        exp = (
            f"$$\\int_{{0}}^{{{a}}}({f})\\,dx=\\left[{anti}\\right]_{{0}}^{{{a}}}={target}$$"
        )
        return q, exp, str(target)
    if level == 2:
        a, b = random.choice([(0, 2), (0, 3), (1, 2), (1, 3), (-1, 2), (0, 1)])
        N = (b ** 3 - a ** 3) + (b * b - a * a) + target * (b - a)
        q = (
            f"$\\displaystyle\\int_{{{a}}}^{{{b}}}(3x^{{2}}+2x+k)\\,dx={N}$일 때, 상수 $k$의 값을 구하시오."
        )
        exp = (
            f"$$\\int_{{{a}}}^{{{b}}}(3x^{{2}}+2x+k)\\,dx=\\left[x^{{3}}+x^{{2}}+kx\\right]_{{{a}}}^{{{b}}}"
            f"={b ** 3 - a ** 3}+{b * b - a * a}+{_coef(b - a, 'k')}={N}$$\n\n"
            f"${_coef(b - a, 'k')}={N - (b ** 3 - a ** 3) - (b * b - a * a)}$이므로 $k={target}$"
        )
        return q, exp, str(target)
    while True:
        m = random.choice([2, 3, 4, 6])
        num = target * m ** 3
        # 넓이 N 이 너무 크지 않게(200 이하) 한다. (target=15, m=6 이면 N=540 이 된다.)
        if num % 6 == 0 and num // 6 <= 200:
            N = num // 6
            break
    if random.random() < 0.5:
        # 교점이 0 이 아닌 곳에 있는 꼴: y = k(x - lo)(hi - x) 의 넓이는 k(hi - lo)³/6
        lo = random.choice([-3, -2, -1, 1, 2, 3])
        hi = lo + m
        q = (
            f"곡선 $y=k{_fac(_lin(1, -lo))}({_lin(-1, hi)})$ ($k>0$)와 $x$축으로 둘러싸인 부분의 넓이가 ${N}$일 때, "
            "상수 $k$의 값을 구하시오."
        )
        area = _frac(m ** 3, 6)
        area_k = "k" if area == "1" else f"{area}k"
        exp = (
            f"곡선과 $x$축의 교점의 $x$좌표는 ${lo}$, ${hi}$이고 "
            "$\\int_{\\alpha}^{\\beta}(x-\\alpha)(\\beta-x)\\,dx=\\frac{(\\beta-\\alpha)^{3}}{6}$이므로 넓이는\n\n"
            f"$$k\\times\\frac{{({hi}-{_p(lo)})^{{3}}}}{{6}}={area_k}={N}$$\n\n따라서 $k={target}$"
        )
        return q, exp, str(target)
    q = f"곡선 $y=kx({m}-x)$ ($k>0$)와 $x$축으로 둘러싸인 부분의 넓이가 ${N}$일 때, 상수 $k$의 값을 구하시오."
    half = _frac(m, 2)
    sq_term = "x^{2}" if half == "1" else f"{half}x^{{2}}"
    area = _frac(m ** 3, 6)
    area_k = "k" if area == "1" else f"{area}k"
    exp = (
        f"곡선과 $x$축의 교점의 $x$좌표는 $0$, ${m}$이므로 넓이는\n\n"
        f"$$\\int_{{0}}^{{{m}}}kx({m}-x)\\,dx=k\\left[{sq_term}-\\frac{{1}}{{3}}x^{{3}}\\right]_{{0}}^{{{m}}}"
        f"={area_k}={N}$$\n\n따라서 $k={target}$"
    )
    return q, exp, str(target)


# ==========================================
# [고3] 확률과 통계
# ==========================================
# ---------- 순열과 조합 ----------
def p_perm(target, level):
    """순열과 조합 (하: 순열·조합의 식, 중: 조합과 순열의 합·차, 상: 조합의 성질 nCr = nC(n-r) 을 쓰는 방정식)."""
    T, T1 = target, target - 1
    if level == 1:
        form = random.choice(["perm", "comb", "frac"])
        if form == "comb":
            N = T * T1 // 2
            q = f"${{}}_{{n}}\\mathrm{{C}}_{{2}}={N}$일 때, 자연수 $n$의 값을 구하시오."
            exp = (
                f"${{}}_{{n}}\\mathrm{{C}}_{{2}}=\\frac{{n(n-1)}}{{2}}$이므로 $\\frac{{n(n-1)}}{{2}}={N}$, 즉 $n(n-1)={2 * N}$입니다.\n\n"
                f"$n^{{2}}-n-{2 * N}=0$, $(n-{T})(n+{T1})=0$이고 $n\\ge 2$이므로 $n={T}$"
            )
            return q, exp, str(T)
        N = T * T1
        if form == "frac":
            q = f"$\\frac{{n!}}{{(n-2)!}}={N}$일 때, 자연수 $n$의 값을 구하시오. (단, $n\\ge 2$)"
            head = "$\\frac{n!}{(n-2)!}=n(n-1)$이므로 "
        else:
            q = f"${{}}_{{n}}\\mathrm{{P}}_{{2}}={N}$일 때, 자연수 $n$의 값을 구하시오."
            head = "${}_{n}\\mathrm{P}_{2}=n(n-1)$이므로 "
        exp = (
            f"{head}$n(n-1)={N}$입니다.\n\n"
            f"$n^{{2}}-n-{N}=0$, $(n-{T})(n+{T1})=0$이고 $n\\ge 2$이므로 $n={T}$"
        )
        return q, exp, str(T)
    if level == 2:
        form = random.choice(["sum", "diff", "double"])
        if form == "diff":
            N = T * T1 // 2
            q = f"${{}}_{{n}}\\mathrm{{P}}_{{2}}-{{}}_{{n}}\\mathrm{{C}}_{{2}}={N}$일 때, 자연수 $n$의 값을 구하시오."
            exp = (
                "${}_{n}\\mathrm{P}_{2}=n(n-1)$, ${}_{n}\\mathrm{C}_{2}=\\frac{n(n-1)}{2}$이므로\n\n"
                f"$$n(n-1)-\\frac{{n(n-1)}}{{2}}=\\frac{{n(n-1)}}{{2}}={N}$$\n\n"
                f"$n(n-1)={T * T1}$이므로 $n={T}$"
            )
        elif form == "double":
            N = 2 * T * T1
            q = f"$2\\times{{}}_{{n}}\\mathrm{{C}}_{{2}}+{{}}_{{n}}\\mathrm{{P}}_{{2}}={N}$일 때, 자연수 $n$의 값을 구하시오."
            exp = (
                "${}_{n}\\mathrm{C}_{2}=\\frac{n(n-1)}{2}$, ${}_{n}\\mathrm{P}_{2}=n(n-1)$이므로\n\n"
                f"$$2\\times\\frac{{n(n-1)}}{{2}}+n(n-1)=2n(n-1)={N}$$\n\n"
                f"$n(n-1)={T * T1}$이므로 $n={T}$"
            )
        else:
            N = 3 * T * T1 // 2
            q = f"${{}}_{{n}}\\mathrm{{C}}_{{2}}+{{}}_{{n}}\\mathrm{{P}}_{{2}}={N}$일 때, 자연수 $n$의 값을 구하시오."
            exp = (
                f"${{}}_{{n}}\\mathrm{{C}}_{{2}}=\\frac{{n(n-1)}}{{2}}$, ${{}}_{{n}}\\mathrm{{P}}_{{2}}=n(n-1)$이므로\n\n"
                f"$$\\frac{{3n(n-1)}}{{2}}={N}$$\n\n$n(n-1)={T * T1}$이므로 $n={T}$"
            )
        return q, exp, str(T)
    # 상: nC(n-2) = nC2, nC(n-1) = nC1 로 바꿔야 식을 풀 수 있다. (nP3 처럼 큰 수가 나오지 않는다.)
    form = random.choice(["two", "three", "shift"])
    if form == "three":
        N = T * (T + 1) // 2 + 1
        q = (
            f"${{}}_{{n}}\\mathrm{{C}}_{{n-2}}+{{}}_{{n}}\\mathrm{{C}}_{{n-1}}+{{}}_{{n}}\\mathrm{{C}}_{{n}}={N}$일 때, "
            "자연수 $n$의 값을 구하시오. (단, $n\\ge 2$)"
        )
        exp = (
            "${}_{n}\\mathrm{C}_{r}={}_{n}\\mathrm{C}_{n-r}$이므로 "
            "${}_{n}\\mathrm{C}_{n-2}={}_{n}\\mathrm{C}_{2}=\\frac{n(n-1)}{2}$, "
            "${}_{n}\\mathrm{C}_{n-1}={}_{n}\\mathrm{C}_{1}=n$, ${}_{n}\\mathrm{C}_{n}=1$입니다.\n\n"
            f"$$\\frac{{n(n-1)}}{{2}}+n+1=\\frac{{n(n+1)}}{{2}}+1={N}$$\n\n"
            f"$n(n+1)={T * (T + 1)}$이므로 $n={T}$"
        )
    elif form == "shift":
        N = T * (T + 1) // 2
        q = f"${{}}_{{n+1}}\\mathrm{{C}}_{{n-1}}={N}$일 때, 자연수 $n$의 값을 구하시오."
        exp = (
            "${}_{n+1}\\mathrm{C}_{n-1}={}_{n+1}\\mathrm{C}_{(n+1)-(n-1)}={}_{n+1}\\mathrm{C}_{2}$이므로\n\n"
            f"$$\\frac{{(n+1)n}}{{2}}={N}$$\n\n$n(n+1)={2 * N}$이므로 $n={T}$"
        )
    else:
        N = T * (T + 1) // 2
        q = (
            f"${{}}_{{n}}\\mathrm{{C}}_{{n-2}}+{{}}_{{n}}\\mathrm{{C}}_{{n-1}}={N}$일 때, 자연수 $n$의 값을 구하시오. "
            "(단, $n\\ge 2$)"
        )
        exp = (
            "${}_{n}\\mathrm{C}_{r}={}_{n}\\mathrm{C}_{n-r}$이므로 "
            "${}_{n}\\mathrm{C}_{n-2}={}_{n}\\mathrm{C}_{2}=\\frac{n(n-1)}{2}$, "
            "${}_{n}\\mathrm{C}_{n-1}={}_{n}\\mathrm{C}_{1}=n$입니다.\n\n"
            f"$$\\frac{{n(n-1)}}{{2}}+n=\\frac{{n(n+1)}}{{2}}={N}$$\n\n"
            f"$n(n+1)={2 * N}$이므로 $n={T}$"
        )
    return q, exp, str(T)


def p_binomial_coef(target, level):
    """이항정리 (하: (1+x)^n 의 x² 계수, 중: (1+2x)^n 의 x² 계수나 x·x² 계수의 합, 상: 계수들의 합으로 n 구하기)."""
    T = target
    if level == 1:
        var = random.choice(["x", "x", "y", "t"])
        N = math.comb(T, 2)
        if random.random() < 0.5:
            q = f"$(1+{var})^{{n}}$의 전개식에서 ${var}^{{2}}$의 계수가 ${N}$일 때, 자연수 $n$의 값을 구하시오."
            head = f"$(1+{var})^{{n}}$의 전개식에서 ${var}^{{2}}$의 계수는 ${{}}_{{n}}\\mathrm{{C}}_{{2}}=\\frac{{n(n-1)}}{{2}}$입니다.\n\n"
        else:
            q = (
                f"$({var}+1)^{{n}}$의 전개식에서 ${var}^{{n-2}}$의 계수가 ${N}$일 때, 자연수 $n$의 값을 구하시오. "
                "(단, $n\\ge 2$)"
            )
            head = (
                f"$({var}+1)^{{n}}$의 전개식에서 ${var}^{{n-2}}$의 계수는 "
                "${}_{n}\\mathrm{C}_{n-2}={}_{n}\\mathrm{C}_{2}=\\frac{n(n-1)}{2}$입니다.\n\n"
            )
        exp = head + f"$\\frac{{n(n-1)}}{{2}}={N}$에서 $n(n-1)={2 * N}$이므로 $n={T}$"
        return q, exp, str(T)
    if level == 2:
        if random.random() < 0.5:
            N = T * (T + 1) // 2
            q = f"$(1+x)^{{n}}$의 전개식에서 $x$의 계수와 $x^{{2}}$의 계수의 합이 ${N}$일 때, 자연수 $n$의 값을 구하시오."
            exp = (
                "$x$의 계수는 ${}_{n}\\mathrm{C}_{1}=n$, $x^{2}$의 계수는 ${}_{n}\\mathrm{C}_{2}=\\frac{n(n-1)}{2}$이므로\n\n"
                f"$$n+\\frac{{n(n-1)}}{{2}}=\\frac{{n(n+1)}}{{2}}={N}$$\n\n$n(n+1)={2 * N}$이므로 $n={T}$"
            )
            return q, exp, str(T)
        k = random.choice([k for k in (2, 3) if math.comb(T, 2) * k * k <= 500])
        N = math.comb(T, 2) * k * k
        q = f"$(1+{k}x)^{{n}}$의 전개식에서 $x^{{2}}$의 계수가 ${N}$일 때, 자연수 $n$의 값을 구하시오."
        exp = (
            f"$(1+{k}x)^{{n}}$의 전개식에서 $x^{{2}}$의 계수는 ${{}}_{{n}}\\mathrm{{C}}_{{2}}\\times{k}^{{2}}"
            f"=\\frac{{n(n-1)}}{{2}}\\times{k * k}$입니다.\n\n"
            f"$\\frac{{n(n-1)}}{{2}}\\times{k * k}={N}$에서 $n(n-1)={T * (T - 1)}$이므로 $n={T}$"
        )
        return q, exp, str(T)
    # 상: x³ 항은 n 이 3 이상일 때만 있으므로 target 이 2 면 쓰지 않는다.
    var = random.choice(["x", "x", "y", "t"])
    sw = lambda s: s.replace("x", var)    # 문제와 풀이의 변수 문자를 함께 바꾼다. (이 구간의 글에는 x 가 변수로만 쓰인다.)
    if T >= 3 and random.random() < 0.5:
        N = math.comb(T, 2) + math.comb(T, 3)
        q = (
            f"$(1+x)^{{n}}$의 전개식에서 $x^{{2}}$의 계수와 $x^{{3}}$의 계수의 합이 ${N}$일 때, "
            "자연수 $n$의 값을 구하시오."
        )
        exp = (
            "$x^{2}$의 계수는 ${}_{n}\\mathrm{C}_{2}$, $x^{3}$의 계수는 ${}_{n}\\mathrm{C}_{3}$이고 "
            "${}_{n}\\mathrm{C}_{2}+{}_{n}\\mathrm{C}_{3}={}_{n+1}\\mathrm{C}_{3}$입니다.\n\n"
            f"${{}}_{{n+1}}\\mathrm{{C}}_{{3}}={N}$이고 ${{}}_{{{T + 1}}}\\mathrm{{C}}_{{3}}={N}$이므로 "
            f"$n+1={T + 1}$, 즉 $n={T}$"
        )
        return sw(q), sw(exp), str(T)
    N = 2 * T * T
    q = f"$(1+2x)^{{n}}$의 전개식에서 $x$의 계수와 $x^{{2}}$의 계수의 합이 ${N}$일 때, 자연수 $n$의 값을 구하시오."
    exp = (
        "$x$의 계수는 ${}_{n}\\mathrm{C}_{1}\\times2=2n$, "
        "$x^{2}$의 계수는 ${}_{n}\\mathrm{C}_{2}\\times2^{2}=2n(n-1)$이므로\n\n"
        f"$$2n+2n(n-1)=2n^{{2}}={N}$$\n\n$n^{{2}}={T * T}$이고 $n$은 자연수이므로 $n={T}$"
    )
    return sw(q), sw(exp), str(T)


# ---------- 확률 ----------
def p_prob_unknown(target, level):
    """확률 (하: 한 개를 꺼낼 때, 중: 복원추출로 두 번, 상: 두 개를 동시에 꺼낼 때 — 이차방정식을 풀어야 한다)."""
    # 중·상은 확률이 제곱·곱 꼴이라 분모가 커지므로 처음 공의 개수를 작게 잡는다.
    a = random.randint(2, 9) if level == 1 else random.randint(2, 5)
    colors = random.choice([("빨간", "파란"), ("흰", "검은"), ("노란", "초록")])
    c1, c2 = colors
    base = f"주머니에 {c1} 공이 ${a}$개, {c2} 공이 $x$개 들어 있다. 이 주머니에서 "
    if level == 1:
        P = Fraction(a, a + target)
        q = (
            base + f"공 한 개를 임의로 꺼낼 때, {c1} 공일 확률이 ${_frac(P.numerator, P.denominator)}$이다. "
            "$x$의 값을 구하시오."
        )
        exp = (
            f"전체 공의 개수는 ${a}+x$이므로 {c1} 공일 확률은 $\\frac{{{a}}}{{{a}+x}}={_frac(P.numerator, P.denominator)}$입니다.\n\n"
            f"${a}+x={a + target}$이므로 $x={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        P = Fraction(a, a + target) ** 2
        q = (
            base + f"공 한 개를 꺼내 색을 확인하고 다시 넣은 후 또 한 개를 꺼낼 때, 두 번 모두 {c1} 공일 확률이 "
            f"${_frac(P.numerator, P.denominator)}$이다. $x$의 값을 구하시오."
        )
        exp = (
            f"한 번 꺼낼 때 {c1} 공일 확률은 $\\frac{{{a}}}{{{a}+x}}$이고, 두 번 모두 {c1} 공일 확률은\n\n"
            f"$$\\left(\\frac{{{a}}}{{{a}+x}}\\right)^{{2}}={_frac(P.numerator, P.denominator)}$$\n\n"
            f"이므로 $\\frac{{{a}}}{{{a}+x}}={_frac(a, a + target)}$, 즉 $x={target}$"
        )
        return q, exp, str(target)
    # 두 개를 동시에 꺼내 모두 c1 공일 확률: C(a,2)/C(a+x,2) = a(a-1) / {(a+x)(a+x-1)}
    # → (a+x)(a+x-1) = a(a-1)/P 를 풀면 연속한 두 자연수의 곱이 되어 a+x 가 나온다.
    tot = a + target
    P = Fraction(a * (a - 1), tot * (tot - 1))
    q = (
        base + f"공 두 개를 동시에 꺼낼 때, 두 공이 모두 {c1} 공일 확률이 ${_frac(P.numerator, P.denominator)}$이다. "
        "$x$의 값을 구하시오."
    )
    exp = (
        f"전체 공은 $({a}+x)$개이므로 두 공이 모두 {c1} 공일 확률은\n\n"
        f"$$\\frac{{{{}}_{{{a}}}\\mathrm{{C}}_{{2}}}}{{{{}}_{{{a}+x}}\\mathrm{{C}}_{{2}}}}"
        f"=\\frac{{{a * (a - 1)}}}{{({a}+x)({a}+x-1)}}={_frac(P.numerator, P.denominator)}$$\n\n"
        f"$({a}+x)({a}+x-1)={tot * (tot - 1)}={tot}\\times{tot - 1}$이므로 ${a}+x={tot}$, 즉 $x={target}$"
    )
    return q, exp, str(target)


# ---------- 통계 ----------
def p_binomial_dist(target, level):
    """이항분포 (하: E(aX+b), 중: V(aX+b), 상: E(aX+b)와 V(aX+b)가 주어질 때 n)."""
    if level == 1:
        def make():
            n = random.choice([6, 8, 9, 10, 12, 15, 18, 20, 24, 30, 36])
            p = random.choice([Fraction(1, 2), Fraction(1, 3), Fraction(2, 3), Fraction(1, 4), Fraction(1, 5)])
            return n, p, random.randint(2, 5), random.randint(-6, 6)

        def solve(s):
            n, p, a, b = s
            v = a * n * p + b
            return int(v) if v.denominator == 1 else None

        n, p, a, b = _roll(make, solve, target)
        q = (
            f"확률변수 $X$가 이항분포 $\\mathrm{{B}}\\left({n}, {_frac(p.numerator, p.denominator)}\\right)$을 따를 때, "
            f"$\\mathrm{{E}}({_lin(a, b, 'X')})$의 값을 구하시오."
        )
        exp = (
            f"$\\mathrm{{E}}(X)={n}\\times{_frac(p.numerator, p.denominator)}={_frac(n * p)}$이므로\n\n"
            f"$$\\mathrm{{E}}({_lin(a, b, 'X')})={a}\\mathrm{{E}}(X) {_sgn(b)}={a}\\times{_frac(n * p)} {_sgn(b)}={target}$$"
        )
        return q, exp, str(target)
    if level == 2:
        def make2():
            n = random.choice([2, 4, 6, 8, 9, 12, 16, 18, 24, 36, 48, 60])
            p = random.choice([Fraction(1, 2), Fraction(1, 3), Fraction(2, 3), Fraction(1, 4), Fraction(3, 4)])
            return n, p, random.randint(1, 4), random.randint(-5, 5)

        def solve2(s):
            n, p, a, b = s
            v = a * a * n * p * (1 - p)
            return int(v) if v.denominator == 1 else None

        n, p, a, b = _roll(make2, solve2, target)
        q = (
            f"확률변수 $X$가 이항분포 $\\mathrm{{B}}\\left({n}, {_frac(p.numerator, p.denominator)}\\right)$을 따를 때, "
            f"$\\mathrm{{V}}({_lin(a, b, 'X')})$의 값을 구하시오."
        )
        V = n * p * (1 - p)
        exp = (
            f"$\\mathrm{{V}}(X)={n}\\times{_frac(p.numerator, p.denominator)}\\times{_frac(1 - p)}={_frac(V)}$이므로\n\n"
            f"$$\\mathrm{{V}}({_lin(a, b, 'X')})={a}^{{2}}\\mathrm{{V}}(X)={a * a}\\times{_frac(V)}={target}$$"
        )
        return q, exp, str(target)

    # 상: n=target, 확률 p 를 정하고 E(aX+b), V(aX+b) 가 모두 정수가 되는 (p, a, b) 를 고른다.
    def make3():
        p = random.choice([Fraction(1, 2), Fraction(1, 3), Fraction(2, 3), Fraction(1, 4), Fraction(3, 4), Fraction(1, 5)])
        return p, random.randint(2, 4), _nonzero(-5, 5)

    def solve3(s):
        p, a, b = s
        e, v = a * target * p + b, a * a * target * p * (1 - p)
        return target if e.denominator == 1 and v.denominator == 1 and v > 0 else None

    p, a, b = _roll(make3, solve3, target)
    EX = Fraction(target) * p
    VX = EX * (1 - p)
    e, v = a * EX + b, a * a * VX
    q = (
        f"확률변수 $X$가 이항분포 $\\mathrm{{B}}(n, p)$를 따르고 "
        f"$\\mathrm{{E}}({_lin(a, b, 'X')})={_frac(e)}$, $\\mathrm{{V}}({_lin(a, b, 'X')})={_frac(v)}$일 때, "
        "$n$의 값을 구하시오."
    )
    exp = (
        f"$\\mathrm{{E}}({_lin(a, b, 'X')})={a}\\mathrm{{E}}(X) {_sgn(b)}={_frac(e)}$이므로 $\\mathrm{{E}}(X)={_frac(EX)}$입니다.\n\n"
        f"$\\mathrm{{V}}({_lin(a, b, 'X')})={a}^{{2}}\\mathrm{{V}}(X)={_frac(v)}$이므로 $\\mathrm{{V}}(X)={_frac(VX)}$입니다.\n\n"
        f"$\\mathrm{{E}}(X)=np$, $\\mathrm{{V}}(X)=np(1-p)$에서 $1-p=\\frac{{\\mathrm{{V}}(X)}}{{\\mathrm{{E}}(X)}}={_frac(1 - p)}$, "
        f"즉 $p={_frac(p.numerator, p.denominator)}$이고 $n=\\frac{{\\mathrm{{E}}(X)}}{{p}}={target}$"
    )
    return q, exp, str(target)


def p_normal(target, level):
    """정규분포의 표준화 (하: 같은 쪽 꼬리, 중: 반대쪽 꼬리(대칭), 상: 대칭성으로 평균을 먼저 구한 뒤 표준화)."""
    def pick(zs, below=False):
        """below=True 이면 평균보다 d 만큼 아래 값(m-d)도 양수가 되도록 평균을 잡는다. (음수 점수가 나오지 않게)"""
        while True:
            z = random.choice(zs)
            d = z * target
            if d.denominator != 1:
                continue
            d = int(d)
            m = random.randint(max(15, d + 5) if below else 10, 100)
            return z, m, d

    sigma_sym = "\\sigma"
    tail = "(단, $Z$는 표준정규분포를 따르는 확률변수이다.)"
    if level == 1:
        z, m, d = pick([Fraction(1), Fraction(2)])
        a = m + d
        q = (
            f"확률변수 $X$가 정규분포 $\\mathrm{{N}}({m}, \\sigma^{{2}})$을 따르고 "
            f"$\\mathrm{{P}}(X\\ge {a})=\\mathrm{{P}}(Z\\ge {_frac(z)})$일 때, 양수 $\\sigma$의 값을 구하시오. "
            f"{tail}"
        )
        exp = (
            f"$X$를 표준화하면 $Z=\\frac{{X-{m}}}{{{sigma_sym}}}$입니다.\n\n"
            f"$\\mathrm{{P}}(X\\ge {a})=\\mathrm{{P}}\\left(Z\\ge\\frac{{{a}-{m}}}{{{sigma_sym}}}\\right)$이므로\n\n"
            f"$$\\frac{{{d}}}{{{sigma_sym}}}={_frac(z)}$$\n\n따라서 $\\sigma={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        z, m, d = pick([Fraction(3, 2), Fraction(1), Fraction(2), Fraction(3)], below=True)
        a = m - d
        q = (
            f"확률변수 $X$가 정규분포 $\\mathrm{{N}}({m}, \\sigma^{{2}})$을 따르고 "
            f"$\\mathrm{{P}}(X\\le {a})=\\mathrm{{P}}(Z\\ge {_frac(z)})$일 때, 양수 $\\sigma$의 값을 구하시오. "
            f"{tail}"
        )
        exp = (
            f"표준정규분포의 확률밀도함수는 $z=0$에 대하여 대칭이므로 $\\mathrm{{P}}(Z\\ge {_frac(z)})=\\mathrm{{P}}(Z\\le -{_frac(z)})$입니다.\n\n"
            f"$X$를 표준화하면 $\\frac{{{a}-{m}}}{{{sigma_sym}}}=-{_frac(z)}$이므로 $\\frac{{{d}}}{{{sigma_sym}}}={_frac(z)}$\n\n"
            f"따라서 $\\sigma={target}$"
        )
        return q, exp, str(target)
    # 상: 평균이 문자 m 이고, P(X≤s1)=P(X≥s2) 에서 m=(s1+s2)/2 를 먼저 구해야 한다.
    z, m, d = pick([Fraction(3, 2), Fraction(1), Fraction(2), Fraction(3)], below=True)
    t = random.randint(3, 12)
    s1, s2 = m - t, m + t
    a = m + d
    q = (
        "확률변수 $X$가 정규분포 $\\mathrm{N}(m, \\sigma^{2})$을 따르고 "
        f"$\\mathrm{{P}}(X\\le {s1})=\\mathrm{{P}}(X\\ge {s2})$, $\\mathrm{{P}}(X\\ge {a})=\\mathrm{{P}}(Z\\ge {_frac(z)})$일 때, "
        f"양수 $\\sigma$의 값을 구하시오. {tail}"
    )
    exp = (
        f"$\\mathrm{{P}}(X\\le {s1})=\\mathrm{{P}}(X\\ge {s2})$이므로 ${s1}$과 ${s2}$는 평균 $m$에 대하여 대칭입니다.\n\n"
        f"$m=\\frac{{{s1}+{s2}}}{{2}}={m}$\n\n"
        f"$\\mathrm{{P}}(X\\ge {a})=\\mathrm{{P}}\\left(Z\\ge\\frac{{{a}-{m}}}{{{sigma_sym}}}\\right)$이므로 "
        f"$\\frac{{{d}}}{{{sigma_sym}}}={_frac(z)}$, 따라서 $\\sigma={target}$"
    )
    return q, exp, str(target)


def p_expectation(target, level):
    """확률분포의 평균과 분산 (하: 세 값의 평균, 중: 확률 하나가 문자인 네 값의 평균, 상: E(aX+b)와 E(X²)으로 V(aX+b))."""
    probsets = {
        3: [[Fraction(1, 2), Fraction(1, 4), Fraction(1, 4)], [Fraction(1, 3), Fraction(1, 3), Fraction(1, 3)],
            [Fraction(1, 2), Fraction(1, 3), Fraction(1, 6)], [Fraction(1, 6), Fraction(1, 3), Fraction(1, 2)]],
        4: [[Fraction(1, 4)] * 4, [Fraction(1, 2), Fraction(1, 4), Fraction(1, 8), Fraction(1, 8)],
            [Fraction(1, 10), Fraction(2, 5), Fraction(3, 10), Fraction(1, 5)],
            [Fraction(1, 6), Fraction(1, 3), Fraction(1, 3), Fraction(1, 6)]],
    }
    if level == 1:
        def make():
            ps = random.choice(probsets[3])
            xs = sorted(random.sample(range(0, 24), 3))
            return xs, ps

        xs, ps = _roll(make, lambda s: sum(x * p for x, p in zip(*s)), target)
        shown = "\n".join(f"- $\\mathrm{{P}}(X={x})={_frac(p.numerator, p.denominator)}$" for x, p in zip(xs, ps))
        q = (
            f"확률변수 $X$가 갖는 값이 ${', '.join(map(str, xs))}$이고, 각 값을 가질 확률이 다음과 같을 때, "
            f"$\\mathrm{{E}}(X)$의 값을 구하시오.\n\n{shown}"
        )
        terms = " + ".join(f"{x}\\times{_frac(p.numerator, p.denominator)}" for x, p in zip(xs, ps))
        exp = f"$$\\mathrm{{E}}(X)=\\sum x_{{i}}p_{{i}}={terms}={target}$$"
        return q, exp, str(target)
    if level == 2:
        def make2():
            ps = random.choice(probsets[4])
            xs = sorted(random.sample(range(0, 24), 4))
            return xs, ps, random.randrange(4)

        xs, ps, hid = _roll(make2, lambda s: sum(x * p for x, p in zip(s[0], s[1])), target)
        rows = []
        for i, (x, p) in enumerate(zip(xs, ps)):
            val = "a" if i == hid else _frac(p.numerator, p.denominator)
            rows.append(f"- $\\mathrm{{P}}(X={x})={val}$")
        others = " + ".join(_frac(p.numerator, p.denominator) for i, p in enumerate(ps) if i != hid)
        q = (
            f"확률변수 $X$가 갖는 값이 ${', '.join(map(str, xs))}$이고, 각 값을 가질 확률이 다음과 같을 때, "
            f"$\\mathrm{{E}}(X)$의 값을 구하시오. (단, $a$는 상수이다.)\n\n" + "\n".join(rows)
        )
        terms = " + ".join(f"{x}\\times{_frac(p.numerator, p.denominator)}" for x, p in zip(xs, ps))
        exp = (
            f"확률의 총합은 $1$이므로 $a=1-({others})={_frac(ps[hid].numerator, ps[hid].denominator)}$입니다.\n\n"
            f"$$\\mathrm{{E}}(X)=\\sum x_{{i}}p_{{i}}={terms}={target}$$"
        )
        return q, exp, str(target)
    # 상: E(aX+b)=e → E(X)=m, V(X)=E(X²)-{E(X)}², V(aX+b)=a²V(X)=target.  (a 가 1 이면 너무 쉬우므로 a≥2)
    a = random.choice([2, 3])
    b = _nonzero(-5, 5)
    m = random.randint(1, 6)
    Vx = Fraction(target, a * a)
    s = m * m + Vx
    e = a * m + b
    q = (
        f"확률변수 $X$에 대하여 $\\mathrm{{E}}({_lin(a, b, 'X')})={e}$, $\\mathrm{{E}}(X^{{2}})={_frac(s)}$일 때, "
        f"$\\mathrm{{V}}({_lin(a, b, 'X')})$의 값을 구하시오."
    )
    exp = (
        f"$\\mathrm{{E}}({_lin(a, b, 'X')})={a}\\mathrm{{E}}(X) {_sgn(b)}={e}$이므로 $\\mathrm{{E}}(X)={m}$입니다.\n\n"
        f"$\\mathrm{{V}}(X)=\\mathrm{{E}}(X^{{2}})-\\{{\\mathrm{{E}}(X)\\}}^{{2}}={_frac(s)}-{m}^{{2}}={_frac(Vx)}$이므로\n\n"
        f"$$\\mathrm{{V}}({_lin(a, b, 'X')})={a}^{{2}}\\mathrm{{V}}(X)={a * a}\\times{_frac(Vx)}={target}$$"
    )
    return q, exp, str(target)


# ==========================================
# 학년별 단원과 단원마다 쓸 문제 유형 (교과서 목차 순서)
# ==========================================
UNIT_FAMILIES = {
    1: [
        ("다항식", [h_remainder, h_expand_coef]),
        ("방정식과 부등식", [h_quad_roots, h_quad_ineq_count]),
        ("행렬", [h_matrix]),
        ("경우의 수", [h_count_multiples, h_dice]),
        ("도형의 방정식", [h_distance, h_circle, h_line]),
        ("집합과 명제", [h_set_count, h_subset, h_prop]),
        ("함수와 그래프", [h_composite, h_inverse, h_rational]),
    ],
    2: [
        ("지수함수와 로그함수", [s_log, s_exp]),
        ("삼각함수", [s_trig]),
        ("수열", [s_seq]),
        ("함수의 극한과 연속", [s_limit]),
        ("미분", [s_deriv]),
        ("적분", [s_integral]),
    ],
    3: [
        ("순열과 조합", [p_perm, p_binomial_coef]),
        ("확률", [p_prob_unknown]),
        ("통계", [p_binomial_dist, p_normal, p_expectation]),
    ],
}


# ==========================================
# 학년·난이도 이름
# ==========================================
GRADE_TITLES = {1: "고등학교 1학년 수학", 2: "고등학교 2학년 수학", 3: "고등학교 3학년 수학"}
DIFFICULTY_LABELS = {1: "하", 2: "중", 3: "상"}
# 난이도 선택 버튼에 적는 예상 플레이타임(분). 문제 푸는 시간이 난이도에 따라 늘어나는 것(하 1 : 중 1.5 : 상 2)을
# 어림해서 잡았다. (문제 3개 약 6~12분 + 타임머신·퍼즐·용의자 지목 등 고정 구간 약 4분) 값만 바꾸면 화면에 반영된다.
DIFFICULTY_PLAYTIME_MIN = {1: 10, 2: 15, 3: 20}


def generate_grade_problems(grade, difficulty=1):
    """학년과 난이도에 맞는 문제 3개. 서로 다른 단원 3개를 뽑고, 정답은 15·9·2 를 섞어서 쓴다."""
    grade = grade if grade in (1, 2) else 3
    title = f"[ {GRADE_TITLES[grade]} · 난이도 {DIFFICULTY_LABELS[difficulty]} ]"

    targets = [15, 9, 2]
    random.shuffle(targets)
    units = random.sample(UNIT_FAMILIES[grade], 3)

    problems = []
    for target, (unit, families) in zip(targets, units):
        question, explanation, answer = random.choice(families)(target, difficulty)
        question, explanation = _tidy_math(_fix_josa(question)), _tidy_math(_fix_josa(explanation))
        problems.append((question, f"*({unit} 단원)*\n\n{explanation}", answer))

    return title, problems


# ==========================================
# 빠른 통과 문제 (문제 선택 화면에 문제 번호 없이 빨간 '매우 어려움' 버튼으로 따로 둔, 학년마다 다른 문제)
#  - 정답이 곧 타임머신 암호 1592 다. 이 문제만 풀어도 타임머신으로 갈 수 있다.
#  - 풀고 나면 다른 문제처럼 "정답을 기억하세요" 화면에서 정답을 보여 준다.
#  - 들어가 보고 어려우면 풀이 화면의 "← 문제 목록으로 돌아가기"로 다시 문제 선택 화면에 갈 수 있다.
#  - 학년(고1·고2·고3)을 고르면 고1은 공통수학1, 고2는 공통수학2, 고3은 미적분 문제가 나온다.
#    (grade 는 학년 선택 버튼에서 저장한다.)
#  - 문제를 바꾸려면 정답이 1592 가 되도록 맞춰야 한다. 정답이 곧 타임머신 암호이기 때문이다.
#    (고1: g(49)=2401-809 / 고2: 798+794 / 고3: f(x)=(e^x+1)^3+C, f(0)=600 에서 C=592, f(ln 9)=1000+592)
#    고3 미적분 문제는 처음 그대로 둔다.
# ==========================================
FAST_PASS_INDEX = 3
# 학년 → (제목, (문제, 풀이, 정답))
FAST_PASS_BY_GRADE = {
    1: (
        "[ 고등학교 1학년 수학 · 공통수학1 ]",
        (
            "삼차방정식 $x^{3}-8x^{2}+ax-b=0$ ($a$, $b$는 상수)의 세 근 $\\alpha,\\ \\beta,\\ \\gamma$가 "
            "$\\alpha^{2}+\\beta^{2}+\\gamma^{2}=48$, $\\alpha^{3}+\\beta^{3}+\\gamma^{3}=335$를 만족시킨다. "
            "$\\alpha^{2},\\ \\beta^{2},\\ \\gamma^{2}$을 세 근으로 하고 최고차항의 계수가 $1$인 삼차식 $g(x)$에 대하여 "
            "$g(49)$의 값을 구하시오.",
            "*(방정식과 부등식 · 삼차방정식의 근과 계수의 관계)*\n\n"
            "근과 계수의 관계에서 $\\alpha+\\beta+\\gamma=8$, $\\alpha\\beta+\\beta\\gamma+\\gamma\\alpha=a$, "
            "$\\alpha\\beta\\gamma=b$입니다.\n\n"
            "$\\alpha^{2}+\\beta^{2}+\\gamma^{2}=8^{2}-2a=48$에서 $a=8$\n\n"
            "$\\alpha^{3}+\\beta^{3}+\\gamma^{3}=8^{3}-3\\times8\\times a+3b=512-192+3b=335$에서 $b=5$\n\n"
            "이제 $\\alpha^{2},\\ \\beta^{2},\\ \\gamma^{2}$의 합, 두 개씩 곱한 것의 합, 곱을 구하면\n\n"
            "$$\\alpha^{2}+\\beta^{2}+\\gamma^{2}=48$$\n\n"
            "$$\\alpha^{2}\\beta^{2}+\\beta^{2}\\gamma^{2}+\\gamma^{2}\\alpha^{2}=a^{2}-2b\\times8=64-80=-16$$\n\n"
            "$$\\alpha^{2}\\beta^{2}\\gamma^{2}=b^{2}=25$$\n\n"
            "이므로 $g(x)=x^{3}-48x^{2}-16x-25$입니다.\n\n"
            "$$g(49)=49^{2}(49-48)-16\\times49-25=2401-784-25=1592$$\n\n"
            "(다른 풀이) $f(x)=x^{3}-8x^{2}+8x-5$라 하면 $g(x^{2})=-f(x)f(-x)$이므로 "
            "$g(49)=-f(7)f(-7)=-2\\times(-796)=1592$",
            "1592",
        ),
    ),
    2: (
        "[ 고등학교 2학년 수학 · 공통수학2 ]",
        (
            "함수 $f(x)=x^{2}-x-397$에 대하여 방정식 $f(f(x))=x$의 서로 다른 모든 실근의 제곱의 합을 구하시오.",
            "*(함수 · 합성함수와 이차방정식)*\n\n"
            "$f(x)=x^{2}-x-397$이므로 임의의 $u$, $v$에 대하여 $f(u)-f(v)=(u-v)(u+v-1)$입니다.\n\n"
            "$u=f(x)$, $v=x$를 넣으면 $f(f(x))-f(x)=\\{f(x)-x\\}\\{f(x)+x-1\\}$이므로\n\n"
            "$$f(f(x))-x=\\{f(f(x))-f(x)\\}+\\{f(x)-x\\}=\\{f(x)-x\\}\\{f(x)+x\\}$$\n\n"
            "따라서 $f(f(x))=x$는 $f(x)=x$ 또는 $f(x)=-x$와 같습니다.\n\n"
            "• $f(x)=x$, 즉 $x^{2}-2x-397=0$: 판별식 $\\dfrac{D}{4}=1+397=398>0$이므로 서로 다른 두 실근을 갖고, "
            "근과 계수의 관계에서 두 근의 제곱의 합은 $2^{2}-2\\times(-397)=798$\n\n"
            "• $f(x)=-x$, 즉 $x^{2}-397=0$: $x=\\pm\\sqrt{397}$이므로 두 근의 제곱의 합은 $397+397=794$\n\n"
            "두 방정식에 공통인 근이 있다면 $x=-x$에서 $x=0$인데 $f(0)=-397\\ne0$이므로 공통인 근은 없습니다. "
            "따라서 서로 다른 실근은 모두 $4$개이고\n\n"
            "$$798+794=1592$$",
            "1592",
        ),
    ),
    3: (
        "[ 고등학교 3학년 수학 · 미적분 ]",
        (
            "함수 $f(x)$가 모든 실수 $x$에 대하여 다음을 만족시킨다.\n\n"
            "$$f'(x)=3e^{x}(e^{x}+1)^{2}$$\n\n"
            "$f(0)=600$일 때, $f(\\ln 9)$의 값을 구하시오.",
            "*(미적분 · 치환적분 단원)*\n\n"
            "$t=e^{x}+1$로 놓으면 $dt=e^{x}dx$이므로\n\n"
            "$$\\int 3e^{x}(e^{x}+1)^{2}dx=\\int 3t^{2}dt=t^{3}+C=(e^{x}+1)^{3}+C$$\n\n"
            "$f(0)=8+C=600$이므로 $C=592$입니다.\n\n"
            "따라서 $f(\\ln 9)=(9+1)^{3}+592=1000+592=1592$",
            "1592",
        ),
    ),
}


def fast_pass_for_grade():
    """지금 고른 학년의 '매우 어려움' 문제: (제목, (문제, 풀이, 정답)). 학년을 모르면 고3 미적분 문제를 쓴다."""
    return FAST_PASS_BY_GRADE.get(st.session_state.get("grade"), FAST_PASS_BY_GRADE[3])


# ==========================================
# Streamlit 메인 로직 및 GUI 구성
# ==========================================
# 브라우저 탭(헤더) 제목: 학년 선택부터 타임머신까지 중생대 화면에서는 "현재 시대 : 중생대",
# 타임머신 도착 뒤의 조선시대 화면(한지 도착 화면·감옥)에서는 "현재 위치 : 조선시대",
# 그 밖의 화면(인트로·난이도 선택)에서는 게임 이름을 보여 준다.
# (타임머신이 움직이는 동안의 제목은 도착 애니메이션 안에서 따로 바꾼다.)
MESOZOIC_STEPS = {
    "select_grade", "select_problem", "solve", "end",
    "stalker_clue", "timemachine",
}
JOSEON_STEPS = {"arrival", "prison_key"}
_current_step = st.session_state.get("step")
if _current_step == "secret_room":
    # 비밀의 방은 오기 직전 화면의 시대를 따른다. (조선시대 화면에서 왔다면 "현재 위치 : 조선시대")
    _current_step = st.session_state.get("secret_room_prev_step", "select_grade")
st.set_page_config(
    page_title=(
        "현재 시대 : 중생대"
        if _current_step in MESOZOIC_STEPS
        else "현재 위치 : 조선시대"
        if _current_step in JOSEON_STEPS
        else "인천수학축전 방탈출게임"
    ),
    layout="centered",
)

# 새로 접속한 세션이면 방문자 수를 1 올린다. (버튼을 눌러 스크립트가 다시 실행돼도 한 번만 센다.)
if "visit_number" not in st.session_state:
    st.session_state.visit_number = record_visit()

# 모든 화면에서 Streamlit 자체 버튼(상단 툴바·메뉴, Manage app 등)을 숨긴다.
hide_streamlit_chrome()
# 스톱워치가 시작된 뒤(난이도를 고른 뒤)에는 모든 화면 우측 상단에 작게 보여 준다.
show_stopwatch()

if "step" not in st.session_state:
    st.session_state.step = "intro"
    st.session_state.problems = []
    st.session_state.current_problem = None
    st.session_state.current_problem_idx = None
    st.session_state.solved_indices = set()
    st.session_state.title = ""
    st.session_state.is_dev_mode = False
    st.session_state.intro_text_done = False
    st.session_state.timemachine_success = False
    st.session_state.arrival_text_done = False
    st.session_state.secret_room_prev_step = "select_grade"

# 이미 열려 있던 세션에도 단서 화면용 상태가 항상 있도록 보장한다.
st.session_state.setdefault("stalker_clue_shown", False)
st.session_state.setdefault("stalker_clue_button_idx", None)
st.session_state.setdefault("select_clue", pick_select_clue())
# 난이도: 1=하(기본), 2=중, 3=상. 난이도 선택 화면에서 고른다.
st.session_state.setdefault("difficulty", 1)

# --- 화면 0: 인트로 ---
if st.session_state.step == "intro":
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-color: #0e1117;
            font-weight: bold !important;
        }}
        .stApp, .stApp * {{
            font-family: {APP_FONT_FAMILY} !important;
        }}
        div[data-testid="stTextInput"] label {{
            display: none !important;
        }}
        .intro-card::before {{
            background-image: url("https://i.postimg.cc/657sHLjT/Agent-Image-A-plain-flat-gray-background-completely-filling-the-frame-overlaid-with-a-subtle-natu.png");
        }}
        .intro-text {{
            color: #1a1a1a !important;
            font-size: 18px !important;
            font-weight: 900 !important;
            /* 굴림체는 굵은 글자꼴이 없어 bold 만으로는 티가 안 나므로, 같은 색의 가는 테두리를 덧대 굵게 보이게 한다. */
            -webkit-text-stroke: 0.6px #1a1a1a;
            /* 네온 느낌: 주황빛이 글자 둘레에 번지도록 번짐 반경이 다른 그림자를 겹친다. (조선시대 도착 화면과 같은 색) */
            text-shadow:
                0 0 3px #ff7a1a,
                0 0 8px #ff7a1a,
                0 0 16px #ff5a00,
                0 0 28px #ff3d00;
            letter-spacing: 1px;
            text-align: center;
            word-break: keep-all;
        }}
        div.stButton > button, div[data-testid="stButton"] > button {{
            background-color: #ffffff !important;
            color: #000000 !important;
            font-weight: bold !important;
            border: 1px solid #cccccc !important;
            font-size: 13px !important;
            padding: 6px 16px !important;
            border-radius: 15px !important;
            box-shadow: 0px 2px 6px rgba(0,0,0,0.4);
            width: auto !important;
        }}
        div.stButton > button:hover, div[data-testid="stButton"] > button:hover {{
            background-color: #e0e0e0 !important;
            color: #000000 !important;
            border-color: #999999 !important;
        }}
        {FULLSCREEN_PHOTO_CSS}
        </style>
        """,
        unsafe_allow_html=True,
    )

    # 이번 접속이 몇 번째 방문인지 사진 위쪽에 작게 보여 준다. (저장에 실패해 번호가 없으면 숨긴다.)
    visitor_html = ""
    if SHOW_VISITOR_COUNT and st.session_state.visit_number:
        visitor_html = f"<div class='visitor-count'><b>{st.session_state.visit_number:,}</b>번째 방문자</div>"

    text_placeholder = st.empty()
    # 줄바꿈은 "\n" 으로 적고, 화면에는 <br> 로 바꿔 보여 준다. (글자를 하나씩 찍는 도중 <br> 태그가 반쯤 보이지 않게)
    full_text = (
        "공룡시대를 탈출하기 위해 세 가지 수학 문제를 해결해야 합니다. 모든 문제를 해결하면 타임머신을 작동할 수 있습니다.\n\n"
        "문제를 푸는 도중 발견하는 용의자에 대한 단서를 잘 기억해 두세요."
    )
    full_html = full_text.replace("\n", "<br>")

    if not st.session_state.get("intro_text_done", False):
        current_text = ""
        for char in full_text:
            current_text += char
            shown_text = current_text.replace("\n", "<br>")
            text_placeholder.markdown(
                f"<div class='photo-card intro-card'>{visitor_html}<span class='intro-text'>{shown_text}</span></div>",
                unsafe_allow_html=True,
            )
            time.sleep(0.15)
        st.session_state.intro_text_done = True
    else:
        text_placeholder.markdown(
            f"<div class='photo-card intro-card'>{visitor_html}<span class='intro-text'>{full_html}</span></div>",
            unsafe_allow_html=True,
        )

    # 버튼은 CSS 로 사진 아래쪽 가운데에 붙는다.
    if lucky_button("다음으로"):
        st.session_state.step = "select_difficulty"
        st.rerun()

# --- 화면 0-1: 난이도 선택 (인트로와 같은 검은 배경, 상·중·하 버튼 3개) ---
#  - 스토커 단서는 문제 선택 화면에서 나온다. 시작 화면과 이 화면에서는 나오지 않는다.
elif st.session_state.step == "select_difficulty":
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-color: #0e1117;
            font-weight: bold !important;
        }}
        .stApp, .stApp * {{
            font-family: {APP_FONT_FAMILY} !important;
        }}
        .difficulty-title {{
            margin: 110px 0 10px 0;
            text-align: center;
            color: #f1f1f1;
            font-size: 26px;
            letter-spacing: 2px;
            word-break: keep-all;
        }}
        .difficulty-note {{
            margin: 0 0 28px 0;
            text-align: center;
            color: #8d93a1;
            font-size: 13px;
            word-break: keep-all;
        }}
        div.stButton > button, div[data-testid="stButton"] > button {{
            background-color: #ffffff !important;
            color: #000000 !important;
            font-weight: 900 !important;
            border: 1px solid #cccccc !important;
            font-size: 17px !important;
            padding: 6px 12px !important;
            border-radius: 12px !important;
            box-shadow: 0px 2px 6px rgba(0,0,0,0.4);
        }}
        /* 버튼 안쪽 글자 요소(<p>)가 따로 가진 얇은 굵기(400)를 덮어써서 글씨를 굵게 한다.
           굴림체는 굵은 글자 모양이 따로 없어서 굵게 지정해도 거의 달라 보이지 않고, 테두리를 덧대면 글자가
           뭉개진다. 그래서 이 세 버튼만 굵은 글자 모양을 가진 폰트(맑은 고딕 등)를 쓴다. */
        div[data-testid="stButton"] > button,
        div[data-testid="stButton"] > button * {{
            font-family: 'Malgun Gothic', '맑은 고딕', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif !important;
        }}
        div[data-testid="stButton"] > button div, div[data-testid="stButton"] > button p {{
            font-size: 17px !important;
            font-weight: 800 !important;
        }}
        /* 버튼 3개를 화면 가운데에 모아서 작게 보이게 한다.
           폰에서는 칸이 세로로 쌓여 버튼이 한 줄을 다 채우므로, 너비에 상한을 두고 가운데 정렬한다. */
        div[data-testid="stHorizontalBlock"] {{
            max-width: 380px;
            margin-left: auto;
            margin-right: auto;
        }}
        div[data-testid="stButton"] {{
            display: flex;
            justify-content: center;
        }}
        div[data-testid="stButton"] > button {{
            width: min(100%, 140px) !important;
        }}
        div.stButton > button:hover, div[data-testid="stButton"] > button:hover {{
            background-color: #e0e0e0 !important;
            color: #000000 !important;
            border-color: #999999 !important;
        }}
        </style>
        <div class="difficulty-title">난이도를 선택하세요</div>
        <div class="difficulty-note">버튼 아래 시간은 예상 플레이타임입니다</div>
        """,
        unsafe_allow_html=True,
    )

    # (표시 이름, 난이도 값): 상=3, 중=2, 하=1. 값이 클수록 문제가 어렵다.
    # 버튼 글자는 두 줄: 난이도 이름 + 작은 글씨의 예상 플레이타임. (줄바꿈은 마크다운의 '공백 두 칸 + 줄바꿈')
    cols = st.columns(3)
    for col, (label, level) in zip(cols, [("상", 3), ("중", 2), ("하", 1)]):
        button_text = f"{label}  \n:small[약 {DIFFICULTY_PLAYTIME_MIN[level]}분]"
        if lucky_button(button_text, use_container_width=True, dg=col, key=f"difficulty_{level}"):
            # 스톱워치를 여기서 시작한다. (난이도를 '선택'한 순간부터. 우측 상단에 작게 보이고, 끝은 마지막 화면의 "다음으로")
            # 이미 시작했다면(setdefault) 시계를 다시 맞추지 않는다.
            st.session_state.setdefault("stopwatch_start", time.monotonic())
            st.session_state.difficulty = level
            st.session_state.step = "select_grade"
            st.rerun()

# --- 화면 5: 타임머신 화면 ---
elif st.session_state.step == "timemachine":
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-image: url("https://i.postimg.cc/44qvqdDc/Agent-Image-The-exact-same-rectangular-brushed-steel-and-copper-time-machine-pod-from-the-referenc.png");
            /* 100% 100% 는 그림을 화면 비율에 억지로 맞춰 늘리므로, 세로로 긴 폰에서 찌그러진다.
               cover 는 비율을 지킨 채 화면을 가득 채우고 남는 부분만 자른다. */
            background-size: cover;
            background-position: center;
            background-repeat: no-repeat;
        }}
        .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
        .stApp p, .stApp span, .stApp div, .stApp markdown {{
            color: #000000 !important;
            font-weight: bold !important;
        }}
        .stApp, .stApp * {{
            font-family: {APP_FONT_FAMILY} !important;
        }}
        /* 라벨 전체 끄는 구문 삭제 후, 텍스트 입력과 비밀번호 버튼만 숨김처리 */
        div[data-testid="stTextInput"] label {{
            display: none !important;
        }}
        button[data-testid="stPasswordInputVisibilityButton"], 
        button[aria-label="View password"], 
        button[aria-label="Hide password"] {{
            display: none !important;
        }}
        div[data-baseweb="input"] {{
            background-color: #ffffff !important;
            border-radius: 8px !important;
            border: 1px solid #cccccc !important;
        }}
        div[data-baseweb="input"] input {{
            color: #000000 !important;
            background-color: #ffffff !important;
            font-weight: bold !important;
        }}
        div.stButton > button, 
        div[data-testid="stButton"] > button, 
        button[kind="secondary"] {{
            background-color: #ffffff !important;
            color: #000000 !important;
            font-weight: bold !important;
            border: 1px solid #cccccc !important;
            border-radius: 8px !important;
        }}
        div.stButton > button:hover, 
        div[data-testid="stButton"] > button:hover, 
        button[kind="secondary"]:hover {{
            background-color: #e0e0e0 !important;
            color: #000000 !important;
        }}
        div.stButton > button[kind="primary"], 
        div[data-testid="stButton"] > button[kind="primary"], 
        div.stButton > button[data-testid="baseButton-primary"] {{
            background-color: #66BB6A !important;
            color: #ffffff !important;
            font-weight: bold !important;
            border: none !important;
        }}
        div.stButton > button[kind="primary"]:hover, 
        div[data-testid="stButton"] > button[kind="primary"]:hover, 
        div.stButton > button[data-testid="baseButton-primary"]:hover {{
            background-color: #4CAF50 !important;
            color: #ffffff !important;
        }}
        {READABLE_TEXT_CSS}
        </style>
        """,
        unsafe_allow_html=True,
    )

    if st.session_state.timemachine_success:
        st.iframe(get_timemachine_animation_html(), height=10)
        time.sleep(TIMEMACHINE_ANIM_DURATION_MS / 1000 + 4)
        st.session_state.arrival_text_done = False
        st.session_state.step = "arrival"
        st.rerun()
    else:
        st.write("")
        st.write("")

        _, mid_col, _ = st.columns([1, 2, 1])

        with mid_col:
            st.markdown(
                """
                <div style="
                    background-color: #ffffff;
                    border-radius: 12px;
                    padding: 14px 20px;
                    text-align: center;
                    margin-bottom: 20px;
                    box-shadow: 0px 4px 12px rgba(0,0,0,0.3);
                    border: 1px solid #cccccc;
                ">
                    <h3 style="font-size: 20px; font-weight: bold; word-break: keep-all; color: #000000; margin: 0;">
                        앞에서 봤던 정답을 큰 순서대로 쓰세요
                    </h3>
                </div>
                """,
                unsafe_allow_html=True,
            )

            tm_answer = st.text_input(
                "정답 입력",
                key="tm_answer_input",
                label_visibility="collapsed",
                placeholder="숫자를 입력하세요",
            )

            if lucky_button("확인 🚀", type="primary", use_container_width=True):
                if tm_answer.strip() == "1592":
                    st.session_state.timemachine_success = True
                    st.rerun()
                else:
                    st.error("정답이 아니에요. 앞에서 본 정답을 다시 확인해 보세요!")

            st.write("")
            st.write("")
            st.markdown("---")
            if lucky_button("← 문제 목록으로 돌아가기", use_container_width=True):
                st.session_state.step = "select_problem"
                st.rerun()

# --- 화면 6: 타임머신 도착 후 화면 ---
elif st.session_state.step == "arrival":
    st.markdown(
        f"""
        <style>
        /* 한지 사진 카드는 조선시대 그림 위가 아니라 검은 배경 위에 둔다.
           (조선시대 그림은 타임머신 도착 애니메이션에서만 보인다.) */
        .stApp {{
            background-color: #000000 !important;
            background-image: none !important;
        }}
        .stApp, .stApp * {{
            font-family: {APP_FONT_FAMILY} !important;
        }}
        div[data-testid="stTextInput"] label {{
            display: none !important;
        }}
        .arrival-card {{
            /* 1 이면 사진을 화면에 딱 맞추고, 1 보다 크면 그만큼 확대해 가장자리를 잘라낸다. */
            --photo-zoom: 1;
        }}
        .arrival-card::before {{
            background-image: url("https://i.postimg.cc/cJBPKDHB/Agent-Image-An-aged-weathered-sheet-of-thick-traditional-Korean-mulberry-paper-hanji-from-the-J.png");
        }}
        .arrival-text {{
            color: #000000 !important;
            font-size: 18px !important;
            font-weight: 900 !important;
            /* 굴림체는 굵은 글자꼴이 없어 bold 만으로는 티가 안 나므로, 같은 색의 가는 테두리를 덧대 굵게 보이게 한다. */
            -webkit-text-stroke: 0.6px #000000;
            /* 네온 느낌: 주황빛이 글자 둘레에 번지도록 번짐 반경이 다른 그림자를 겹친다. */
            text-shadow:
                0 0 3px #ff7a1a,
                0 0 8px #ff7a1a,
                0 0 16px #ff5a00,
                0 0 28px #ff3d00;
            letter-spacing: 1px;
            text-align: center;
            word-break: keep-all;
        }}
        div.stButton > button, div[data-testid="stButton"] > button {{
            background-color: #ffffff !important;
            color: #000000 !important;
            font-weight: bold !important;
            border: 1px solid #cccccc !important;
            font-size: 15px !important;
            padding: 8px 22px !important;
            border-radius: 15px !important;
            box-shadow: 0px 2px 6px rgba(0,0,0,0.4);
        }}
        div.stButton > button:hover, div[data-testid="stButton"] > button:hover {{
            background-color: #e0e0e0 !important;
            color: #000000 !important;
            border-color: #999999 !important;
        }}
        {FULLSCREEN_PHOTO_CSS}
        </style>
        """,
        unsafe_allow_html=True,
    )

    text_placeholder = st.empty()
    # 줄바꿈은 "\n" 으로 적고, 화면에는 <br> 로 바꿔 보여 준다. (글자를 하나씩 찍는 도중 <br> 태그가 반쯤 보이지 않게)
    full_text = (
        "여기는 조선시대입니다!\n\n"
        "여러분은 시간여행 도중 이곳에 도착했지만, 우주 대스타와 함께 감옥에 갇혀 있습니다."
    )
    full_html = full_text.replace("\n", "<br>")

    if not st.session_state.get("arrival_text_done", False):
        current_text = ""
        for char in full_text:
            current_text += char
            shown_text = current_text.replace("\n", "<br>")
            text_placeholder.markdown(
                f"<div class='photo-card arrival-card'><span class='arrival-text'>{shown_text}</span></div>",
                unsafe_allow_html=True,
            )
            time.sleep(0.15)
        st.session_state.arrival_text_done = True
    else:
        text_placeholder.markdown(
            f"<div class='photo-card arrival-card'><span class='arrival-text'>{full_html}</span></div>",
            unsafe_allow_html=True,
        )

    # 버튼은 CSS 로 사진 아래쪽 가운데에 붙는다.
    if st.session_state.arrival_text_done:
        if lucky_button("다음으로"):
            st.session_state.step = "prison_key"
            st.rerun()

# --- 화면 7: 감옥 열쇠 퍼즐 (파이프 퍼즐을 풀어야 열쇠를 시도할 수 있다) ---
elif st.session_state.step == "prison_key":
    st.markdown(
        """
        <style>
        .stApp {
            background-color: #120c09 !important;
            background-image: none !important;
        }
        .stApp h2, .stApp p, .stApp li {
            color: #f3e9dc !important;
        }
        /* 감옥 화면은 위·아래에 빈 공간 없이 화면을 꽉 채운다.
           페이지 안쪽 여백과, 비어 있는 요소 사이에도 생기는 요소 간격(gap)을 모두 없앤다. */
        .block-container {
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }
        [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {
            gap: 0 !important;
        }
        /* iframe 은 기본이 글자처럼 놓이는 inline 이라 아래에 글줄 여백(약 8px)이 남아 맨 아래가 스크롤된다. 블록으로 둔다. */
        iframe {
            display: block !important;
            margin-bottom: 0px !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # 스톱워치: 난이도를 고른 뒤 지금까지 흐른 시간을 게임 화면에 넘겨 준다.
    # 화면 내용(srcdoc)이 다시 실행될 때마다 바뀌면 게임이 처음부터 다시 로드되므로, 한 번만 만들어 둔다.
    if "prison_key_html" not in st.session_state:
        started = st.session_state.get("stopwatch_start", time.monotonic())
        elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
        st.session_state.prison_key_html = PRISON_KEY_PUZZLE_HTML.replace(
            "__ELAPSED_BEFORE_MS__", str(elapsed_ms)
        ).replace("__PRISON_CLUE__", html_escape(prison_clue_text()))
    st.iframe(st.session_state.prison_key_html, height=900)

# --- 비밀의 방: 어떤 버튼이든 2만분의 1 확률로 누르면 도착하는 화면 ---
elif st.session_state.step == "secret_room":
    st.markdown(
        f"""
        <style>
        .stApp {{
            background:
                radial-gradient(ellipse at 50% 30%, #4a2a12 0%, #1a0e07 55%, #0a0604 100%) !important;
        }}
        .stApp, .stApp * {{
            font-family: {APP_FONT_FAMILY} !important;
        }}
        .secret-room {{
            max-width: 560px;
            margin: 90px auto 28px auto;
            text-align: center;
            color: #f3e9dc;
        }}
        .secret-room .sr-title {{
            font-size: 34px;
            font-weight: bold;
            letter-spacing: 4px;
            color: #fce080;
            text-shadow: 0 0 22px rgba(252, 224, 128, 0.55);
            margin-bottom: 18px;
            /* 아래 글과의 간격은 그대로 두고, 제목만 위로 올린다. */
            position: relative;
            top: -30px;
        }}
        .secret-room .sr-text {{
            font-size: 17px;
            line-height: 1.8;
            word-break: keep-all;
        }}
        div.stButton > button, div[data-testid="stButton"] > button {{
            background-color: #ffffff !important;
            color: #000000 !important;
            font-weight: bold !important;
            border: 1px solid #cccccc !important;
            border-radius: 8px !important;
        }}
        div.stButton > button:hover, div[data-testid="stButton"] > button:hover {{
            background-color: #e0e0e0 !important;
            color: #000000 !important;
        }}
        /* 돌아가기 버튼은 작게 만들고, 비밀번호를 맞게 넣기 전에는 흐리게 잠가 둔다. */
        .st-key-secret_room_back {{
            align-self: center;
        }}
        div[data-testid="stButton"] > button {{
            width: auto !important;
            min-width: 110px;
            min-height: 32px !important;
            padding: 4px 20px !important;
            font-size: 14px !important;
        }}
        div[data-testid="stButton"] > button:disabled {{
            opacity: 0.35 !important;
            cursor: not-allowed !important;
        }}
        div[data-testid="stTextInput"] {{
            width: 240px;
            max-width: 100%;
            margin: 0 auto;
        }}
        div[data-baseweb="input"] input {{
            text-align: center;
        }}
        button[data-testid="stPasswordInputVisibilityButton"],
        button[aria-label="View password"],
        button[aria-label="Show password"],
        button[aria-label="Hide password"] {{
            display: none !important;
        }}
        </style>
        <div class="secret-room">
            <div class="sr-title">Lucky!</div>
            <div class="sr-text">
                축하합니다. 당신은 1 / 20,000 (0.005%)을 뚫었습니다.<br>
                현재 부스에 있는 운영팀에게 이 사실을 알려주세요!
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    # 풍선은 도착했을 때 한 번만 날린다. (비밀번호를 넣을 때마다 스크립트가 다시 실행되기 때문)
    if not st.session_state.get("secret_room_celebrated"):
        st.balloons()
        st.session_state.secret_room_celebrated = True

    # 감옥 퍼즐 화면에서 이곳으로 왔다면 돌아갈 때 게임이 새로 로드되므로, 그때의 스톱워치 값을 다시 만든다.
    st.session_state.pop("prison_key_html", None)

    # 비밀의 방 안의 버튼은 다시 주사위를 굴리지 않는 일반 버튼이다.
    # 돌아가기 버튼은 비밀번호를 맞게 넣어야 눌린다. (Enter 를 눌러야 입력이 반영된다.)
    secret_pw = st.text_input(
        "비밀번호",
        type="password",
        key="secret_room_pw",
        label_visibility="collapsed",
        placeholder="비밀번호 입력 후 Enter",
    )
    secret_unlocked = secret_pw == SECRET_ROOM_PASSWORD
    if secret_pw and not secret_unlocked:
        st.error("비밀번호가 맞지 않아요. 운영팀에게 문의해 주세요.")
    if st.button(
        "돌아가기" if secret_unlocked else "🔒 돌아가기",
        key="secret_room_back",
        disabled=not secret_unlocked,
    ):
        del st.session_state["secret_room_pw"]
        st.session_state.pop("secret_room_celebrated", None)
        st.session_state.step = st.session_state.get(
            "secret_room_prev_step", "select_grade"
        )
        st.rerun()

# --- 단서 화면: 검은 화면에 단서(녹색 장갑 또는 파란색 시계)를 보여 준 뒤 원래 화면으로 자동 복귀 ---
elif st.session_state.step == "stalker_clue":
    st.markdown(
        f"""
        <style>
        .stApp {{
            background: #000000 !important;
        }}
        .stalker-clue {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            z-index: 999999;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 22px;
            padding: 24px;
            box-sizing: border-box;
            background: #000000;
        }}
        .stalker-clue-count {{
            color: #8a8f98 !important;
            font-family: {APP_FONT_FAMILY} !important;
            font-size: 16px;
            font-weight: bold;
            line-height: 1;
        }}
        .stalker-clue-text {{
            color: #ffffff !important;
            font-family: {APP_FONT_FAMILY} !important;
            font-size: clamp(22px, 6vw, 34px);
            font-weight: bold;
            letter-spacing: 2px;
            line-height: 1.6;
            text-align: center;
            word-break: keep-all;
            text-wrap: balance;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    # 문장은 그대로 두고 그 아래 숫자만 1초마다 바꿔서 남은 시간(5, 4, 3, 2, 1)을 보여 준다.
    clue_box = st.empty()
    clue_text = html_escape(st.session_state.get("clue_text", STALKER_CLUE_TEXTS["green"]))
    for seconds_left in range(STALKER_CLUE_SECONDS, 0, -1):
        clue_box.markdown(
            '<div class="stalker-clue">'
            f'<span class="stalker-clue-text">{clue_text}</span>'
            f'<span class="stalker-clue-count">{seconds_left}</span>'
            "</div>",
            unsafe_allow_html=True,
        )
        time.sleep(1)

    # 시간이 다 되면 단서를 띄우기 직전의 화면으로 돌아간다.
    # 다음 화면이 그려질 때까지 마지막 숫자 '1' 이 멈춘 채 남아 있지 않도록, 문구부터 지운다.
    clue_box.empty()
    st.session_state.step = st.session_state.get("clue_return_step", "select_problem")
    st.rerun()

# --- 그 외 모든 화면 (중생대 문제 선택 및 풀이 등) ---
else:
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-color: #f4f4f4 !important; /* 이미지 로딩 실패 시 글씨가 보이도록 밝은 배경 설정 */
            background-image: url("https://i.postimg.cc/fyDLcqj7/Agent-Image-A-sw-eeping-cinematic-vista-of-the-Mesozoic-era-at-golden-hour-a-herd-of-long-necked-sa.png");
            background-size: cover;
            background-position: center center;
            background-repeat: no-repeat;
        }}
        .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6, 
        .stApp p, .stApp span, .stApp div, .stApp markdown {{
            color: #000000 !important;
            font-weight: bold !important;
        }}
        .stApp, .stApp * {{
            font-family: {APP_FONT_FAMILY} !important;
        }}
        div[data-testid="stTextInput"] label {{
            display: none !important;
        }}
        button[data-testid="stPasswordInputVisibilityButton"], 
        button[aria-label="View password"], 
        button[aria-label="Hide password"] {{
            display: none !important;
        }}
        div[data-baseweb="input"] {{
            background-color: #ffffff !important;
            border-radius: 8px !important;
            border: 1px solid #cccccc !important;
        }}
        div[data-baseweb="input"] input {{
            color: #000000 !important;
            background-color: #ffffff !important;
            font-weight: bold !important;
        }}
        div.stButton > button, 
        div[data-testid="stButton"] > button, 
        button[kind="secondary"] {{
            background-color: #ffffff !important;
            color: #000000 !important;
            font-weight: bold !important;
            border: 1px solid #cccccc !important;
            border-radius: 8px !important;
        }}
        div.stButton > button:hover, 
        div[data-testid="stButton"] > button:hover, 
        button[kind="secondary"]:hover {{
            background-color: #e0e0e0 !important;
            color: #000000 !important;
        }}
        div.stButton > button[kind="primary"], 
        div[data-testid="stButton"] > button[kind="primary"], 
        div.stButton > button[data-testid="baseButton-primary"] {{
            background-color: #66BB6A !important;
            color: #ffffff !important;
            font-weight: bold !important;
            border: none !important;
        }}
        div.stButton > button[kind="primary"]:hover, 
        div[data-testid="stButton"] > button[kind="primary"]:hover, 
        div.stButton > button[data-testid="baseButton-primary"]:hover {{
            background-color: #4CAF50 !important;
            color: #ffffff !important;
        }}
        {READABLE_TEXT_CSS}
        </style>
        """,
        unsafe_allow_html=True,
    )

    # --- 화면 1: 학년 선택 ---
    if st.session_state.step == "select_grade":
        readable_box(
            "<h2>현재 시대 : 중생대</h2><p>본인의 현재 학년을 선택해주세요.</p>",
            gap_below=True,
        )

        col1, col2, col3 = st.columns(3)
        if lucky_button("고1 문제", use_container_width=True, dg=col1):
            st.session_state.grade = 1
            st.session_state.title, st.session_state.problems = (
                generate_grade_problems(1, st.session_state.difficulty)
            )
            st.session_state.solved_indices = set()
            reset_stalker_clue()
            st.session_state.step = "select_problem"
            st.rerun()
        if lucky_button("고2 문제", use_container_width=True, dg=col2):
            st.session_state.grade = 2
            st.session_state.title, st.session_state.problems = (
                generate_grade_problems(2, st.session_state.difficulty)
            )
            st.session_state.solved_indices = set()
            reset_stalker_clue()
            st.session_state.step = "select_problem"
            st.rerun()
        if lucky_button("고3 문제", use_container_width=True, dg=col3):
            st.session_state.grade = 3
            st.session_state.title, st.session_state.problems = (
                generate_grade_problems(3, st.session_state.difficulty)
            )
            st.session_state.solved_indices = set()
            reset_stalker_clue()
            st.session_state.step = "select_problem"
            st.rerun()

        st.write("")
        st.write("")
        st.write("")
        st.session_state.is_dev_mode = st.checkbox(
            "개발자 옵션 (정답 및 풀이 확인 기능 켜기)",
            value=st.session_state.is_dev_mode,
        )

    # --- 화면 2: 문제 선택 ---
    elif st.session_state.step == "select_problem":
        readable_box(
            f"<h3>{html_escape(st.session_state.title)}</h3>"
            "<p>3문제를 모두 풀거나, 매우 어려움 문제를 풀면 아래에 버튼이 생깁니다</p>"
        )
        st.markdown("---")

        # 학년 문제(번호 0, 1, 2) + 따로 떨어진 빨간 '매우 어려움' 문제(고른 학년의 문제, 번호 FAST_PASS_INDEX) 1개.
        # (이 문제의 정답은 타임머신 암호 1592 다.) 학년 문제가 몇 개든 '매우 어려움' 번호는 그대로이다.
        fast_title, fast_problem = fast_pass_for_grade()
        n_problems = min(3, len(st.session_state.problems))
        entries = dict(enumerate(st.session_state.problems[:3]))
        entries[FAST_PASS_INDEX] = fast_problem

        def solved_card(label, ans, color):
            """풀고 난 문제 칸: 완료 표시와 정답을 보여 주는 흰 카드."""
            st.markdown(
                f"""
                <div style="
                    background-color: rgba(255, 255, 255, 0.85);
                    border: 2px solid {color};
                    border-radius: 10px;
                    padding: 12px 8px;
                    text-align: center;
                    box-shadow: 0px 4px 10px rgba(0,0,0,0.2);
                ">
                    <div style="color: {color}; font-size: 13px; font-weight: bold; margin-bottom: 4px;">✅ {label} 완료</div>
                    <div style="color: #000000; font-size: 18px; font-weight: bold;">정답: {ans}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        def open_problem(i):
            """i번 문제 풀이 화면으로 간다. (단서를 아직 못 봤다면 먼저 검은 단서 화면으로 간다.)"""
            if i == FAST_PASS_INDEX:
                # 매우 어려움 문제로 곧장 넘어가도 단서를 놓치지 않도록, 아직 못 봤다면 먼저 보여 준다.
                if not st.session_state.stalker_clue_shown:
                    go_stalker_clue()
            elif is_stalker_clue_button(i):
                # 무작위로 뽑힌 '단서 버튼'을 처음 누르면 문제 대신 검은 단서 화면으로 간다.
                go_stalker_clue()
            st.session_state.current_problem = entries[i]
            st.session_state.current_problem_idx = i
            st.session_state.current_title = (
                fast_title if i == FAST_PASS_INDEX else st.session_state.title
            )
            st.session_state.step = "solve"
            st.rerun()

        cols = st.columns(3)
        for i in range(n_problems):
            with cols[i]:
                if i in st.session_state.solved_indices:
                    solved_card(f"문제 {i+1}", entries[i][2], "#2e7d32")
                elif lucky_button(f"문제 {i+1} 📝", key=f"prob_btn_{i}", use_container_width=True):
                    open_problem(i)

        # 학년별 '매우 어려움' 문제: 문제 번호 없이 빨간 버튼으로 따로 둔다.
        st.markdown(
            """
            <style>
            .st-key-fast_pass_btn div[data-testid="stButton"] > button {
                background-color: #d32f2f !important;
                border: 1px solid #b71c1c !important;
            }
            .st-key-fast_pass_btn div[data-testid="stButton"] > button:hover {
                background-color: #b71c1c !important;
                border-color: #7f0000 !important;
            }
            .st-key-fast_pass_btn div[data-testid="stButton"] > button,
            .st-key-fast_pass_btn div[data-testid="stButton"] > button *,
            .st-key-fast_pass_btn div[data-testid="stButton"] > button:hover * {
                color: #ffffff !important;
                font-weight: 900 !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        st.write("")
        _, hard_col, _ = st.columns([1, 1, 1])
        with hard_col:
            if FAST_PASS_INDEX in st.session_state.solved_indices:
                solved_card("매우 어려움", fast_problem[2], "#c62828")
            elif lucky_button("매우 어려움", key="fast_pass_btn", use_container_width=True):
                open_problem(FAST_PASS_INDEX)

        # 학년 문제를 모두 풀었거나(문제가 있을 때만), 매우 어려움 문제를 풀었으면 타임머신으로 갈 수 있다.
        solved = st.session_state.solved_indices
        all_grade_solved = n_problems > 0 and set(range(n_problems)) <= solved
        if FAST_PASS_INDEX in solved or all_grade_solved:
            st.write("")
            st.write("")
            st.markdown("---")
            if lucky_button(
                "🚀 타임머신 타러 가기", type="primary", use_container_width=True
            ):
                st.session_state.step = "timemachine"
                st.session_state.timemachine_success = False
                st.rerun()

    # --- 화면 3: 문제 풀이 ---
    elif st.session_state.step == "solve":
        current_q, current_exp, current_ans = st.session_state.current_problem

        # 제목 · 구분선 · 문제(수식 포함)를 흰 상자 하나에 담는다.
        readable_markdown(
            f"<h3>{html_escape(st.session_state.get('current_title', st.session_state.title))}</h3>\n\n<hr>\n\n{current_q}"
        )
        st.write("")

        user_answer = st.text_input(
            "정답을 입력하세요 (숫자만 입력):",
            key="user_answer",
            label_visibility="collapsed",
            placeholder="정답을 입력하세요 (숫자만 입력)",
        )
        st.markdown("---")

        if st.session_state.is_dev_mode:
            if "dev_panel_open" not in st.session_state:
                st.session_state.dev_panel_open = False

            if lucky_button("🔒 정답 및 풀이 확인 (암호 입력)", key="dev_panel_toggle"):
                st.session_state.dev_panel_open = not st.session_state.dev_panel_open

            if st.session_state.dev_panel_open:
                pw = st.text_input(
                    "암호를 입력하세요", type="password", key="pw_single"
                )
                if pw == "0805":
                    st.success("암호가 확인되었습니다.")
                    readable_markdown(
                        f"▶ **정답:** {current_ans}\n\n▶ **풀이:**\n{current_exp}"
                    )
                elif pw != "":
                    st.error("암호가 맞지 않아요. 다시 입력해 주세요.")

        st.write("")
        col1, col2, col3 = st.columns([6, 2, 2])
        # '매우 어려움' 문제는 들어와 보고 어려우면 풀지 않고 문제 선택 화면으로 돌아갈 수 있다.
        if st.session_state.current_problem_idx == FAST_PASS_INDEX:
            with col1:
                if lucky_button(
                    "← 문제 목록으로 돌아가기", key="fast_pass_back", use_container_width=True
                ):
                    if "pw_single" in st.session_state:
                        del st.session_state["pw_single"]
                    st.session_state.dev_panel_open = False
                    st.session_state.step = "select_problem"
                    st.rerun()
        with col3:
            if lucky_button(
                "완료 ❯", type="primary", use_container_width=True
            ):
                if user_answer.strip() == current_ans:
                    if "pw_single" in st.session_state:
                        del st.session_state["pw_single"]
                    st.session_state.dev_panel_open = False
                    if st.session_state.current_problem_idx is not None:
                        st.session_state.solved_indices.add(
                            st.session_state.current_problem_idx
                        )
                    st.session_state.step = "end"
                    st.rerun()
                else:
                    add_wrong_answer_penalty()
                    st.error(f"아쉽지만 정답이 아니에요. 다시 풀어 보세요! (+{WRONG_ANSWER_PENALTY_SECONDS}초)")

    # --- 화면 4: 완료 화면 ---
    elif st.session_state.step == "end":
        current_ans = st.session_state.current_problem[2]

        readable_box(
            "<h2>정답을 기억하세요</h2>"
            f"<h3>정답: {html_escape(str(current_ans))}</h3>"
        )
        st.markdown("---")

        if lucky_button("문제 목록으로 돌아가기 📝", use_container_width=True):
            st.session_state.step = "select_problem"
            st.rerun()
