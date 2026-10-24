import json
import math
import os
import random
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
#    넣으면 켜진다. 저장 이름은 UPSTASH_COUNTER_KEY 로 바꿀 수 있다. (기본값: visitor_count_ms)
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
    return base, token, _secret("UPSTASH_COUNTER_KEY") or "visitor_count_ms"


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
#    누를 때마다 딱 한 번씩 500분의 1 확률로 주사위를 굴린다.
#  - 당첨되면 그 버튼의 원래 동작은 실행되지 않고 곧바로 비밀의 방으로 이동한다.
#  - 동작을 확인해 보고 싶으면 SECRET_ROOM_ODDS 를 1 로 바꾸면 항상 이동한다.
#    (감옥 퍼즐 화면 쪽 확률은 아래 PRISON_KEY_PUZZLE_HTML 안의 SECRET_ROOM_ODDS)
# ==========================================
SECRET_ROOM_ODDS = 500
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
    """st.button과 똑같이 쓰되, 클릭될 때마다 500분의 1 확률로 비밀의 방으로 보낸다.

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
    padding: 6px 18px;
    border-radius: 999px;
    background: rgba(0, 0, 0, 0.55);
    color: #f1f1f1;
    font-size: 15px !important;
    letter-spacing: 0.06em;
    white-space: nowrap;
}
.photo-card .visitor-count b {
    margin-right: 2px;
    color: #ffd27a;
    font-size: 22px;
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
    min-width: 200px;
}

/* 안내 글씨(인트로·조선시대 도착 안내): 궁서체 + 무지개빛 네온.
   - 네온: 무지개 여섯 색 그림자가 글자 둘레를 둥글게 돌며 번쩍인다. (여섯 그림자가 60도씩 떨어져 함께 돈다)
     한 바퀴 도는 시간은 neon-orbit 의 4s, 도는 반지름은 --neon-r 이다.
     색의 진하기는 아래 여섯 색의 rgba 마지막 숫자(투명도 0.4)로 정한다. 1 에 가까울수록 진하고 0 에 가까울수록 희미하다.
   - 글꼴: 컴퓨터에 깔린 궁서체를 쓴다. (윈도우 '궁서체', 맥 'Gungseo') 깔려 있지 않은 기기에서는 기본 명조체로 보인다. */
@property --neon-a {
    syntax: '<angle>';
    inherits: true;
    initial-value: 0deg;
}
@keyframes neon-orbit {
    to { --neon-a: 360deg; }
}
.stApp .intro-text, .stApp .arrival-text {
    font-family: '궁서체', '궁서', GungsuhChe, Gungsuh, Gungseo, serif !important;
    --neon-r: 5px;
    animation: neon-orbit 4s linear infinite;
    text-shadow:
        calc(cos(var(--neon-a)) * var(--neon-r)) calc(sin(var(--neon-a)) * var(--neon-r)) 5px rgba(255, 42, 42, 0.4),
        calc(cos(var(--neon-a) + 60deg) * var(--neon-r)) calc(sin(var(--neon-a) + 60deg) * var(--neon-r)) 5px rgba(255, 154, 0, 0.4),
        calc(cos(var(--neon-a) + 120deg) * var(--neon-r)) calc(sin(var(--neon-a) + 120deg) * var(--neon-r)) 5px rgba(255, 230, 0, 0.4),
        calc(cos(var(--neon-a) + 180deg) * var(--neon-r)) calc(sin(var(--neon-a) + 180deg) * var(--neon-r)) 5px rgba(34, 224, 106, 0.4),
        calc(cos(var(--neon-a) + 240deg) * var(--neon-r)) calc(sin(var(--neon-a) + 240deg) * var(--neon-r)) 5px rgba(30, 144, 255, 0.4),
        calc(cos(var(--neon-a) + 300deg) * var(--neon-r)) calc(sin(var(--neon-a) + 300deg) * var(--neon-r)) 5px rgba(180, 76, 255, 0.4);
}
"""


# 인트로·조선시대 도착 안내의 글자 찍는 속도: 한 글자당 쉬는 시간(초). 예전 0.15초의 3.5배 빠르게. 숫자를 키우면 느려진다.
TYPING_DELAY_SECONDS = 0.15 / 3.5


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
# 정답 입력칸의 자동 완성 끄기
#  - 이전에 입력한 답이 키보드 위쪽에 '자동 완성' 칩으로 뜨는 것을 막는다. (입력한 글자는 그대로 보인다.)
#  - Streamlit 의 autocomplete="off" 만으로는 안드로이드 크롬·삼성 키보드가 무시하는 경우가 있어서,
#    Streamlit 이 따로 열어 주지 않는 속성(autocorrect·autocapitalize·spellcheck)도 스크립트로 끈다.
#    이 네 가지가 모두 꺼져 있으면 크롬이 키보드에 '추천 단어 없음'을 알려 주어 추천 줄이 사라진다.
#  - 자동 완성 기록은 입력칸의 name 을 열쇠로 모으므로, name 을 눌러 줄 때마다 새로 바꿔 이어지지 않게 한다.
#  - 대상은 입력칸 위에 붙는 이름(aria-label)으로 찾는다. 정답 칸의 이름을 바꾸면 아래 SELECTOR 도 같이 바꾼다.
# ==========================================
NO_AUTOFILL_JS_HTML = r"""
<script>
(function () {
  var SELECTOR = 'input[aria-label="정답을 입력하세요 (숫자만 입력):"], input[aria-label="정답 입력"]';

  function freshName() { return 'a' + Math.random().toString(36).slice(2, 10); }

  function patch(el) {
    if (el.getAttribute('data-no-autofill')) return;
    el.setAttribute('data-no-autofill', '1');
    el.setAttribute('autocomplete', 'off');
    el.setAttribute('autocorrect', 'off');
    el.setAttribute('autocapitalize', 'off');
    el.setAttribute('spellcheck', 'false');
    el.setAttribute('data-lpignore', 'true');      // LastPass
    el.setAttribute('data-1p-ignore', 'true');     // 1Password
    el.setAttribute('data-bwignore', 'true');      // Bitwarden
    el.setAttribute('data-form-type', 'other');    // Dashlane
    el.setAttribute('name', freshName());
    el.addEventListener('focus', function () { el.setAttribute('name', freshName()); });
  }

  function sweep(doc) {
    try { doc.querySelectorAll(SELECTOR).forEach(patch); } catch (e) {}
  }

  function watch(doc) {
    sweep(doc);
    try {
      new MutationObserver(function () { sweep(doc); }).observe(doc.documentElement, { childList: true, subtree: true });
    } catch (e) {}
  }

  try { watch(window.parent.document); } catch (e) {}

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


def disable_answer_autofill():
    """정답 입력칸에 이전 답이 자동 완성으로 뜨지 않게 한다. (모든 화면에서 한 번 부르면 된다.)"""
    st.iframe(NO_AUTOFILL_JS_HTML, height=1)


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
WRONG_ANSWER_PENALTY_SECONDS = 60


def penalty_label():
    """오답 벌칙 시간을 화면에 적을 글자로. 60초 → '1분', 90초 → '90초'."""
    s = WRONG_ANSWER_PENALTY_SECONDS
    return f"{s // 60}분" if s % 60 == 0 else f"{s}초"

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
    if st.session_state.stalker_clue_button_idx is None:
        st.session_state.stalker_clue_button_idx = random.randrange(3)
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
# 타임머신 이후 화면에 삽입되는 미니게임: 나무 파이프 슬라이딩 퍼즐 (난이도별 하 4x4 · 중 5x5 · 상 6x6)
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

  /* ---------- 비밀의 방 (아무 버튼이든 500분의 1 확률로 열린다) ---------- */
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
        <span aria-hidden="true">💧</span> 물길 연결 하기
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
          <span id="txtLevelNum" class="text-xs sm:text-base font-extrabold text-amber-100">__GRID_SIZE__x__GRID_SIZE__ - 스테이지 1</span>
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
  <div class="sr-text">축하합니다. 당신은 1 / 500 (0.2%)을 뚫었습니다.<br>현재 부스에 있는 운영팀에게 이 사실을 알려주세요!</div>
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
  // 한 변의 칸 수: 파이썬이 난이도에 맞춰 채워 준다. (하=4, 중=5, 상=6, 상에서 2분 이내 도착이면 10 / pipe_puzzle_grid_size)
  const PUZZLE_GRID_SIZE = Number('__GRID_SIZE__') || 4;
  let canvas, ctx;
  let boardGrid = [];
  let boardSize = PUZZLE_GRID_SIZE;
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

    // (r,c) 에서 아직 안 지나간 칸만 밟고 도착 칸까지 갈 수 있는지 본다.
    // 갈 수 없는 쪽으로 들어가면 막다른 곳을 끝없이 되짚게 되어 10x10 처럼 큰 판에서는 화면이 멈추므로, 들어가기 전에 거른다.
    function canReachEnd(r,c){
      const seen = new Set([r*GRID_SIZE+c]);
      const stack = [[r,c]];
      while(stack.length){
        const [cr,cc] = stack.pop();
        if(cr===GRID_SIZE-1 && cc===GRID_SIZE-1) return true;
        for(const [dr,dc] of [[0,1],[1,0],[-1,0],[0,-1]]){
          const nr=cr+dr, nc=cc+dc, nidx=nr*GRID_SIZE+nc;
          if(nr>=0 && nr<GRID_SIZE && nc>=0 && nc<GRID_SIZE && !visited.has(nidx) && !seen.has(nidx)){
            seen.add(nidx); stack.push([nr,nc]);
          }
        }
      }
      return false;
    }

    function findPath(r,c,currentPath){
      const idx = r*GRID_SIZE+c;
      currentPath.push({r,c,idx}); visited.add(idx);
      if(r===GRID_SIZE-1 && c===GRID_SIZE-1){ path=[...currentPath]; return true; }
      const dirs=[{dr:0,dc:1},{dr:1,dc:0},{dr:-1,dc:0},{dr:0,dc:-1}];
      dirs.sort(()=>Math.random()-.5);
      for(const d of dirs){
        const nr=r+d.dr, nc=c+d.dc, nidx=nr*GRID_SIZE+nc;
        if(nr>=0 && nr<GRID_SIZE && nc>=0 && nc<GRID_SIZE && !visited.has(nidx) && canReachEnd(nr,nc)){
          if(findPath(nr,nc,currentPath)) return true;
        }
      }
      currentPath.pop(); visited.delete(idx); return false;
    }
    // 경로가 모든 칸을 지나가면 빈칸을 둘 자리가 없으므로(5x5 처럼 칸 수가 홀수일 때 생길 수 있다) 다시 찾는다.
    do{ path = []; visited = new Set(); findPath(0,0,[]); }while(path.length >= totalCells);

    const grid = new Array(totalCells).fill(null);

    const startOutDir = getDirection(0,0, path[1].r, path[1].c);
    const startPipe = getFittingPipeType(0, startOutDir);
    grid[0] = { type:startPipe.type, rot:startPipe.rot, movable:false, rotatable:false, isStart:true };

    const prevPathNode = path[path.length-2];
    const endInDir = getDirection(GRID_SIZE-1,GRID_SIZE-1, prevPathNode.r, prevPathNode.c);
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

  function generateSolvableLevel(levelId, minMoves){
    levelId = levelId || 1; minMoves = minMoves || 8;
    const GRID_SIZE = PUZZLE_GRID_SIZE;
    const totalCells = GRID_SIZE*GRID_SIZE;

    let grid = null;
    let rebuildAttempts = 0;
    do{
      grid = buildCandidateGrid(GRID_SIZE, totalCells);
      let shuffleAttempts = 0;
      let connected = true;
      do{
        scrambleGrid(grid, GRID_SIZE, Math.round(totalCells*2 + Math.random()*totalCells*1.2));
        connected = solvePipePath(grid, GRID_SIZE).isConnected;
        shuffleAttempts++;
      } while(connected && shuffleAttempts < 15);
      rebuildAttempts++;
      if(!connected) break;
    } while(rebuildAttempts < 40);

    return { id:levelId, title:`${GRID_SIZE}x${GRID_SIZE} 퍼즐 - 스테이지 ${levelId}`, size:GRID_SIZE, minMoves, grid };
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
      level = generateSolvableLevel(levelId, minMoves);
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
    document.getElementById('txtLevelNum').textContent = `${boardSize}x${boardSize} - 스테이지 1`;
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
     비밀의 방: 이 화면의 어떤 버튼이든 누를 때마다 500분의 1 확률로
     원래 동작 대신 비밀의 방이 열린다. (1 로 바꾸면 항상 열린다)
  ======================================================== */
  const SECRET_ROOM_ODDS = 500;
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


DIFFICULTY_LABELS = {1: "하", 2: "중", 3: "상"}
# 난이도 선택 버튼에 적는 예상 플레이타임(분). 문제 푸는 시간이 난이도에 따라 늘어나는 것(하 1 : 중 1.5 : 상 2)을
# 어림해서 잡았다. (문제 3개 약 6~12분 + 타임머신·퍼즐·용의자 지목 등 고정 구간 약 4분) 값만 바꾸면 화면에 반영된다.
DIFFICULTY_PLAYTIME_MIN = {1: 10, 2: 15, 3: 20}
# 물길 연결(파이프) 퍼즐 한 변의 칸 수: 하=4x4, 중=5x5, 상=6x6. (감옥 열쇠 퍼즐 화면이 난이도에 맞춰 쓴다)
PIPE_PUZZLE_GRID_SIZE = {1: 4, 2: 5, 3: 6}
# 난이도 '상'에서 스톱워치 합계(오답 벌칙 시간 포함)가 이 시간 이하인 채로 퍼즐에 도착하면 퍼즐이 더 크게 나온다.
PIPE_PUZZLE_FAST_SECONDS = 120
PIPE_PUZZLE_FAST_GRID_SIZE = 10


def pipe_puzzle_grid_size(difficulty, total_ms):
    """물길 연결 퍼즐 한 변의 칸 수. 난이도 '상'이고 스톱워치 합계(total_ms, 밀리초)가 PIPE_PUZZLE_FAST_SECONDS 이하면 10x10."""
    if difficulty == 3 and total_ms <= PIPE_PUZZLE_FAST_SECONDS * 1000:
        return PIPE_PUZZLE_FAST_GRID_SIZE
    return PIPE_PUZZLE_GRID_SIZE[difficulty]


# ==========================================
# 학년별 단원과 단원마다 쓸 문제 유형 (초등학교 5·6학년)
#  - 문제 유형은 f(target, level) 함수이고 (문제, 풀이, 정답) 을 돌려준다. level: 1=하, 2=중, 3=상.
#  - 정답(target)에서 거꾸로 수를 정해 만들거나, 무작위로 뽑은 조건의 정답이 target 과 같아질 때까지 다시 뽑는다.
#  - 문제와 풀이는 마크다운이고 수식은 $...$ 안에 쓴다. (Streamlit 이 바로 그려 준다.)
#  - 난이도: 하는 단원의 기본 문제, 중은 한 단계를 더 거치는 문제, 상은 여러 단계를 거치거나 조건이 많은 문제.
# ==========================================
_X = "\\times"
_D = "\\div"


def _roll(make, solve, target, tries=30000):
    """make() 로 무작위 조건을 뽑고 solve(조건) 이 target 과 같을 때까지 다시 뽑는다. (make 가 None 을 주면 건너뛴다.)"""
    for _ in range(tries):
        cond = make()
        if cond is not None and solve(cond) == target:
            return cond
    raise ValueError("조건을 찾지 못했다")


def _pick(make, tries=30000):
    """make() 가 None 이 아닌 조건을 줄 때까지 다시 뽑는다."""
    for _ in range(tries):
        cond = make()
        if cond is not None:
            return cond
    raise ValueError("조건을 찾지 못했다")


def _divisors(n):
    return [d for d in range(1, n + 1) if n % d == 0]


def _frac(n, d):
    """분수 수식 글자 (\\dfrac)."""
    return f"\\dfrac{{{n}}}{{{d}}}"


def _fm(x):
    """분수(Fraction)를 대분수 또는 분수 수식 글자로."""
    x = Fraction(x)
    whole, rest = divmod(x.numerator, x.denominator)
    if rest == 0:
        return str(whole)
    return f"{whole}{_frac(rest, x.denominator)}" if whole else _frac(x.numerator, x.denominator)


def _dec(n, scale=10):
    """n / scale 을 소수 글자로 (소수 끝의 0 은 뗀다)."""
    s = f"{n / scale:.{len(str(scale)) - 1}f}"
    return s.rstrip("0").rstrip(".")


# ---------- 초5 ----------
def e5_calc(target, level):
    """자연수의 혼합 계산."""
    if level == 1:
        b, c = random.randint(2, 9), random.randint(2, 9)
        low = max(1, target - b * c + 1)
        a = random.randint(low, low + 15)
        d = a + b * c - target
        q = f"다음을 계산하시오.\n\n$${a} + {b} {_X} {c} - {d}$$"
        exp = (
            f"곱셈을 먼저 계산합니다. ${b} {_X} {c}={b * c}$이므로 "
            f"${a}+{b * c}-{d}={a + b * c}-{d}={target}$입니다."
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            m = random.randint(1, 9)
            pairs = [(a, b) for a in range(2, 13) for b in range(a, 13) if a * b == target + m]
            if pairs:
                break
        a, b = random.choice(pairs)
        e = random.randint(2, 6)
        c = random.randint(1, m * e - 1)
        d = m * e - c
        q = f"다음을 계산하시오.\n\n$${a} {_X} {b} - ({c} + {d}) {_D} {e}$$"
        exp = (
            f"괄호 안을 먼저 계산하면 ${c}+{d}={m * e}$입니다. 곱셈과 나눗셈을 뺄셈보다 먼저 계산하므로 "
            f"${a} {_X} {b}={a * b}$, ${m * e} {_D} {e}={m}$이고 ${a * b}-{m}={target}$입니다."
        )
        return q, exp, str(target)
    m, p = random.randint(2, 6), random.randint(2, 5)
    while True:
        n = random.choice([n for n in range(1, 13) if (target * m + n) % p == 0])
        inner = (target * m + n) // p
        if inner >= 2:
            break
    r = random.randint(1, min(9, inner - 1))
    s = inner - r
    q = f"□ 안에 알맞은 수를 구하시오.\n\n$$(\\square {_X} {m} + {n}) {_D} {p} - {r} = {s}$$"
    exp = (
        f"거꾸로 생각합니다. $(\\square {_X} {m} + {n}) {_D} {p} = {s}+{r}={inner}$이므로 "
        f"$\\square {_X} {m} + {n} = {inner} {_X} {p} = {inner * p}$이고, "
        f"$\\square {_X} {m} = {inner * p}-{n}={inner * p - n}$입니다. "
        f"따라서 □ $={inner * p - n} {_D} {m}={target}$입니다."
    )
    return q, exp, str(target)


def e5_multiple(target, level):
    """약수와 배수."""
    if level == 1:
        k = random.randint(2, 9)
        n = k * target + random.randint(0, k - 1)
        q = f"1부터 {n}까지의 자연수 중에서 {k}의 배수는 모두 몇 개입니까?"
        exp = (
            f"${n} {_D} {k}={target}\\cdots{n % k}$이므로 {k}의 배수는 "
            f"{k}, {k * 2}, {k * 3}, ..., {k * target}로 모두 ${target}$개입니다."
        )
        return q, exp, str(target)
    if level == 2:
        p, q_ = random.choice([(p, q) for p in range(2, 10) for q in range(p + 1, 10) if math.gcd(p, q) == 1])
        a, b = target * p, target * q_
        q = f"두 수 {a}와 {b}의 최대공약수는 얼마입니까?"
        exp = (
            f"${a}={target} {_X} {p}$, ${b}={target} {_X} {q_}$이고 {p}와 {q_}는 1 말고는 공약수가 없습니다. "
            f"따라서 최대공약수는 ${target}$입니다."
        )
        return q, exp, str(target)
    a, b = random.choice([(4, 6), (6, 8), (6, 9), (4, 10), (8, 12), (6, 10), (9, 12), (10, 15), (12, 18), (4, 14)])
    lcm = a * b // math.gcd(a, b)
    n = lcm * target + random.randint(0, lcm - 1)
    q = f"1부터 {n}까지의 자연수 중에서 {a}의 배수도 되고 {b}의 배수도 되는 수는 모두 몇 개입니까?"
    exp = (
        f"{a}와 {b}의 공배수는 최소공배수 ${lcm}$의 배수입니다. "
        f"${n} {_D} {lcm}={target}\\cdots{n % lcm}$이므로 모두 ${target}$개입니다."
    )
    return q, exp, str(target)


def e5_rule(target, level):
    """규칙과 대응."""
    d = random.randint(2, 3) if level == 1 else random.randint(3, 5) if level == 2 else random.randint(4, 8)
    a = random.randint(2, 9)
    head = (
        f"바둑돌을 규칙에 따라 늘어놓고 있습니다. 1번째에는 {a}개, 2번째에는 {a + d}개, "
        f"3번째에는 {a + 2 * d}개를 놓았습니다. "
    )
    if level < 3:
        m = a + d * (target - 1)
        q = head + f"바둑돌을 {m}개 놓은 것은 몇 번째입니까?"
        exp = (
            f"바둑돌이 {d}개씩 늘어납니다. 1번째보다 ${m}-{a}={m - a}$(개) 더 많고 "
            f"${m - a} {_D} {d}={target - 1}$이므로, 1번째에서 {target - 1}번 늘어난 {target}번째입니다."
        )
        return q, exp, str(target)
    m = a + d * (target - 1) + random.randint(0, d - 1)
    q = head + f"바둑돌이 {m}개 있습니다. 이 바둑돌로 만들 수 있는 모양 중 가장 뒤의 것은 몇 번째 모양입니까? (모양은 하나씩 따로 만듭니다.)"
    exp = (
        f"{d}개씩 늘어나므로 n번째 모양에는 ${a}+{d} {_X} (n-1)$개가 필요합니다. "
        f"${m}-{a}={m - a}$, ${m - a} {_D} {d}={target - 1}\\cdots{(m - a) % d}$이므로 "
        f"n-1은 {target - 1}까지 될 수 있고, 가장 뒤의 모양은 {target}번째입니다."
    )
    return q, exp, str(target)


def e5_fraction(target, level):
    """약분과 통분, 분수의 덧셈과 뺄셈."""
    if level == 1:
        a = random.randint(1, 6)
        c = a + target + 1
        b = c + random.randint(1, 6)
        q = f"분모가 {b}인 분수 중에서 ${_frac(a, b)}$보다 크고 ${_frac(c, b)}$보다 작은 분수는 모두 몇 개입니까?"
        exp = (
            f"분모가 같으므로 분자만 비교합니다. 분자는 {a}보다 크고 {c}보다 작은 수이므로 "
            f"{a + 1}, {a + 2}, ..., {c - 1}로 모두 ${c - a - 1}$개입니다."
        )
        return q, exp, str(target)
    if level == 2:
        def make():
            d1, d2 = random.randint(2, 9), random.randint(2, 9)
            if d1 == d2:
                return None
            big = d1 * d2 // math.gcd(d1, d2) * random.randint(1, 6)
            x, y = random.randint(1, d1 - 1), random.randint(1, d2 - 1)
            return (d1, d2, big, x, y) if Fraction(x, d1) < Fraction(y, d2) else None

        d1, d2, big, x, y = _roll(make, lambda c: c[4] * c[2] // c[1] - c[3] * c[2] // c[0] - 1, target)
        n1, n2 = x * big // d1, y * big // d2
        q = f"${_frac(x, d1)}$보다 크고 ${_frac(y, d2)}$보다 작은 분수 중에서 분모가 {big}인 분수는 모두 몇 개입니까?"
        exp = (
            f"분모를 {big}으로 통분하면 ${_frac(x, d1)}={_frac(n1, big)}$, ${_frac(y, d2)}={_frac(n2, big)}$입니다. "
            f"분자는 {n1}보다 크고 {n2}보다 작은 수이므로 {n1 + 1}부터 {n2 - 1}까지 모두 ${n2 - n1 - 1}$개입니다."
        )
        return q, exp, str(target)

    def make():
        b = random.choice([12, 15, 16, 18, 20, 24, 30])
        d, f = random.choice([2, 3, 4, 5, 6, 8]), random.choice([2, 3, 4, 5, 6, 8])
        c, e = random.randint(1, d - 1), random.randint(1, f - 1)
        return (b, c, d, e, f) if Fraction(e, f) > Fraction(c, d) else None

    def solve(cond):
        b, c, d, e, f = cond
        return math.ceil((Fraction(e, f) - Fraction(c, d)) * b) - 1

    b, c, d, e, f = _roll(make, solve, target)
    gap = Fraction(e, f) - Fraction(c, d)
    bound = gap * b
    q = (
        "□ 안에 들어갈 수 있는 자연수는 모두 몇 개입니까?\n\n"
        f"$$\\dfrac{{\\square}}{{{b}}} + {_frac(c, d)} < {_frac(e, f)}$$"
    )
    exp = (
        f"${_frac(e, f)}-{_frac(c, d)}={_fm(gap)}$이므로 $\\dfrac{{\\square}}{{{b}}} < {_fm(gap)}$입니다. "
        f"양쪽에 {b}를 곱하면 □ $< {_fm(bound)}$이므로 □ 안에 들어갈 수 있는 자연수는 "
        f"1부터 {target}까지 모두 ${target}$개입니다."
    )
    return q, exp, str(target)


def e5_area(target, level):
    """다각형의 둘레와 넓이."""
    if level == 1:
        if random.random() < 0.5:
            w = random.randint(3, 20)
            area = w * target
            q = f"넓이가 {area} cm²이고 가로가 {w} cm인 직사각형의 세로는 몇 cm입니까?"
            exp = f"(직사각형의 넓이)=(가로)×(세로)이므로 (세로)$={area} {_D} {w}={target}$(cm)입니다."
        else:
            b = random.randint(3, 20)
            area = b * target
            q = f"밑변의 길이가 {b} cm이고 넓이가 {area} cm²인 평행사변형의 높이는 몇 cm입니까?"
            exp = f"(평행사변형의 넓이)=(밑변)×(높이)이므로 (높이)$={area} {_D} {b}={target}$(cm)입니다."
        return q, exp, str(target)
    if level == 2:
        kind = random.choice(["triangle", "rhombus", "perimeter"])
        if kind == "perimeter":
            w = random.randint(3, 20)
            perimeter = 2 * (w + target)
            q = f"둘레가 {perimeter} cm이고 가로가 {w} cm인 직사각형의 세로는 몇 cm입니까?"
            exp = (
                f"(가로)+(세로)는 둘레의 반이므로 ${perimeter} {_D} 2={perimeter // 2}$(cm)입니다. "
                f"(세로)$={perimeter // 2}-{w}={target}$(cm)입니다."
            )
            return q, exp, str(target)
        side = random.choice([x for x in range(4, 21) if (x * target) % 2 == 0])
        area = side * target // 2
        if kind == "triangle":
            q = f"밑변의 길이가 {side} cm이고 넓이가 {area} cm²인 삼각형의 높이는 몇 cm입니까?"
            exp = (
                f"(삼각형의 넓이)=(밑변)×(높이)÷2이므로 (밑변)×(높이)$={area} {_X} 2={area * 2}$입니다. "
                f"(높이)$={area * 2} {_D} {side}={target}$(cm)입니다."
            )
        else:
            q = f"한 대각선의 길이가 {side} cm이고 넓이가 {area} cm²인 마름모의 다른 대각선의 길이는 몇 cm입니까?"
            exp = (
                f"(마름모의 넓이)=(한 대각선)×(다른 대각선)÷2이므로 두 대각선의 곱은 ${area} {_X} 2={area * 2}$입니다. "
                f"(다른 대각선)$={area * 2} {_D} {side}={target}$(cm)입니다."
            )
        return q, exp, str(target)
    if random.random() < 0.5:
        u = random.randint(3, 12)
        h = random.choice([4, 6, 8, 10, 12])
        area = (u + target) * h // 2
        q = f"윗변이 {u} cm, 높이가 {h} cm이고 넓이가 {area} cm²인 사다리꼴의 아랫변의 길이는 몇 cm입니까?"
        exp = (
            f"(사다리꼴의 넓이)=((윗변)+(아랫변))×(높이)÷2이므로 (윗변)+(아랫변)$={area} {_X} 2 {_D} {h}={(u + target)}$입니다. "
            f"(아랫변)$={u + target}-{u}={target}$(cm)입니다."
        )
    else:
        while True:
            u, v = random.randint(3, 12), random.randint(3, 12)
            if (u + v) * target % 2 == 0:
                break
        area = (u + v) * target // 2
        q = f"윗변이 {u} cm, 아랫변이 {v} cm이고 넓이가 {area} cm²인 사다리꼴의 높이는 몇 cm입니까?"
        exp = (
            f"(윗변)+(아랫변)$={u}+{v}={u + v}$이고 넓이가 {area} cm²이므로 "
            f"(높이)$={area} {_X} 2 {_D} {u + v}={target}$(cm)입니다."
        )
    return q, exp, str(target)


def e5_range(target, level):
    """수의 범위와 어림하기."""
    n = random.randint(10, 60)
    if level == 1:
        m = n + target - 1
        q = f"{n} 이상 {m} 이하인 자연수는 모두 몇 개입니까?"
        exp = f"{n}부터 {m}까지이므로 ${m}-{n}+1={target}$(개)입니다."
        return q, exp, str(target)
    if level == 2:
        m = n + target + 1
        q = f"{n} 초과 {m} 미만인 자연수는 모두 몇 개입니까?"
        exp = f"{n}보다 크고 {m}보다 작으므로 {n + 1}부터 {m - 1}까지입니다. ${m - 1}-{n + 1}+1={target}$(개)입니다."
        return q, exp, str(target)
    k = random.randint(3, 9)
    n = random.randint(20, 120)
    first = -(-n // k) * k
    last = first + (target - 1) * k
    m = last + 1 + random.randint(0, k - 1)
    q = f"{n} 이상 {m} 미만인 자연수 중에서 {k}의 배수는 모두 몇 개입니까?"
    exp = (
        f"{n} 이상 {m - 1} 이하인 수 중에서 {k}의 배수 중 가장 작은 수는 {first}, 가장 큰 수는 {last}입니다. "
        f"${last}-{first}={last - first}$, ${last - first} {_D} {k}={target - 1}$이므로 "
        f"{k}의 배수는 ${target - 1}+1={target}$(개)입니다."
    )
    return q, exp, str(target)


def e5_fracmul(target, level):
    """분수의 곱셈."""
    if level == 1:
        p = random.choice([d for d in _divisors(target) if d <= 9])
        q_ = random.choice([x for x in range(p + 1, p + 9) if math.gcd(p, x) == 1])
        k = target // p
        n = q_ * k
        q = f"{n}의 ${_frac(p, q_)}$는 얼마입니까?"
        exp = f"${n} {_X} {_frac(p, q_)} = ({n} {_D} {q_}) {_X} {p} = {k} {_X} {p} = {target}$입니다."
        return q, exp, str(target)
    if level == 2:
        def make():
            p, q_ = random.randint(1, 9), random.randint(2, 9)
            if p == q_ or math.gcd(p, q_) != 1 or (target * p) % q_:
                return None
            return p, q_

        p, q_ = _pick(make)
        m = target * p // q_
        q = f"어떤 수에 ${_frac(p, q_)}$를 곱했더니 {m}이 되었습니다. 어떤 수는 얼마입니까?"
        exp = (
            f"어떤 수를 □라 하면 □ $ {_X} {_frac(p, q_)} = {m}$입니다. "
            f"□ $={m} {_D} {_frac(p, q_)} = {m} {_X} {_frac(q_, p)} = {target}$입니다."
        )
        return q, exp, str(target)

    def make():
        b, d = random.randint(2, 9), random.randint(2, 9)
        a, c = random.randint(1, b - 1), random.randint(1, d - 1)
        rest = Fraction(b - a, b) * Fraction(d - c, d)
        n = Fraction(target) / rest
        if n.denominator != 1 or not 12 <= n <= 300:
            return None
        n = int(n)
        if (n * a) % b:
            return None
        rest1 = n - n * a // b
        if (rest1 * c) % d:
            return None
        return n, a, b, c, d

    n, a, b, c, d = _pick(make)
    day1 = n * a // b
    rest1 = n - day1
    day2 = rest1 * c // d
    q = (
        f"동화책을 읽고 있습니다. 전체 {n}쪽 중에서 첫째 날에는 전체의 ${_frac(a, b)}$를 읽고, "
        f"둘째 날에는 남은 쪽수의 ${_frac(c, d)}$를 읽었습니다. 아직 읽지 않은 쪽수는 몇 쪽입니까?"
    )
    exp = (
        f"첫째 날 읽은 쪽수는 ${n} {_X} {_frac(a, b)}={day1}$(쪽)이므로 남은 쪽수는 ${n}-{day1}={rest1}$(쪽)입니다. "
        f"둘째 날 읽은 쪽수는 ${rest1} {_X} {_frac(c, d)}={day2}$(쪽)이므로 "
        f"아직 읽지 않은 쪽수는 ${rest1}-{day2}={target}$(쪽)입니다."
    )
    return q, exp, str(target)


def e5_avg(target, level):
    """평균."""
    if level < 3:
        count = 3 if level == 1 else random.choice([4, 5])
        while True:
            others = [random.randint(6, 30) for _ in range(count - 1)]
            if (sum(others) + target) % count == 0:
                break
        mean = (sum(others) + target) // count
        listed = ", ".join(str(v) for v in others)
        if level == 1:
            q = f"{count}개의 수 {listed}, □의 평균이 {mean}입니다. □ 안에 알맞은 수를 구하시오."
        else:
            q = (
                f"지우가 {count}개월 동안 읽은 책은 차례로 {listed}권이고 마지막 달에는 □권을 읽었습니다. "
                f"{count}개월 동안 한 달 평균 {mean}권을 읽었다면 마지막 달에 읽은 책은 몇 권입니까?"
            )
        exp = (
            f"(평균)×(개수)=(합)이므로 {count}개의 합은 ${mean} {_X} {count}={mean * count}$입니다. "
            f"□ $={mean * count}-({'+'.join(str(v) for v in others)})={mean * count}-{sum(others)}={target}$입니다."
        )
        return q, exp, str(target)

    def make():
        n, b = random.randint(3, 7), random.randint(8, 30)
        total = b * (n + 1) - target
        if total % n or total // n < 5:
            return None
        return n, total // n, b

    n, a, b = _pick(make)
    q = (
        f"턱걸이 기록을 재었더니 {n}명의 평균이 {a}회였습니다. 한 명이 더 와서 기록을 재었더니 "
        f"{n + 1}명의 평균이 {b}회가 되었습니다. 새로 온 학생의 기록은 몇 회입니까?"
    )
    exp = (
        f"처음 {n}명의 기록의 합은 ${a} {_X} {n}={a * n}$(회), {n + 1}명의 기록의 합은 ${b} {_X} {n + 1}={b * (n + 1)}$(회)입니다. "
        f"새로 온 학생의 기록은 ${b * (n + 1)}-{a * n}={target}$(회)입니다."
    )
    return q, exp, str(target)


def e5_cuboid(target, level):
    """직육면체."""
    if level == 1:
        q = f"모서리 길이의 합이 {12 * target} cm인 정육면체의 한 모서리의 길이는 몇 cm입니까?"
        exp = f"정육면체는 길이가 같은 모서리가 12개이므로 ${12 * target} {_D} 12={target}$(cm)입니다."
        return q, exp, str(target)
    if level == 2:
        a, b = random.randint(2, 15), random.randint(2, 15)
        s = 4 * (a + b + target)
        q = f"가로가 {a} cm, 세로가 {b} cm인 직육면체의 모서리 길이의 합이 {s} cm입니다. 이 직육면체의 높이는 몇 cm입니까?"
        exp = (
            f"직육면체에는 가로, 세로, 높이 길이의 모서리가 각각 4개씩 있습니다. "
            f"(가로)+(세로)+(높이)$={s} {_D} 4={s // 4}$이므로 (높이)$={s // 4}-{a}-{b}={target}$(cm)입니다."
        )
        return q, exp, str(target)
    k, h = random.randint(2, 4), random.randint(2, 12)
    s = 4 * ((k + 1) * target + h)
    q = (
        f"직육면체의 가로는 세로의 {k}배이고 높이는 {h} cm입니다. "
        f"모서리 길이의 합이 {s} cm일 때, 이 직육면체의 세로는 몇 cm입니까?"
    )
    exp = (
        f"(가로)+(세로)+(높이)$={s} {_D} 4={s // 4}$이므로 (가로)+(세로)$={s // 4}-{h}={s // 4 - h}$입니다. "
        f"가로가 세로의 {k}배이므로 (세로)의 {k + 1}배가 {s // 4 - h}이고, (세로)$={s // 4 - h} {_D} {k + 1}={target}$(cm)입니다."
    )
    return q, exp, str(target)


# ---------- 초6 ----------
def e6_fracdiv(target, level):
    """분수의 나눗셈."""
    if level == 1:
        def make():
            q_, p = random.randint(2, 9), random.randint(1, 9)
            if p == q_ or math.gcd(p, q_) != 1 or (target * p) % q_:
                return None
            n = target * p // q_
            return (n, p, q_) if n >= 2 else None

        n, p, q_ = _pick(make)
        q = f"다음을 계산하시오.\n\n$${n} {_D} {_frac(p, q_)}$$"
        exp = (
            f"자연수를 분수로 나눌 때는 나누는 분수의 분모와 분자를 바꾸어 곱합니다. "
            f"${n} {_D} {_frac(p, q_)} = {n} {_X} {_frac(q_, p)} = {target}$입니다."
        )
        return q, exp, str(target)
    if level == 2:
        def make():
            d = random.randint(3, 9)
            c = random.randint(1, d - 1)
            if math.gcd(c, d) != 1:
                return None
            x = Fraction(target * c, d)
            return (x, c, d) if x.denominator != 1 else None

        x, c, d = _pick(make)
        q = f"다음을 계산하시오.\n\n$${_fm(x)} {_D} {_frac(c, d)}$$"
        exp = (
            f"대분수는 가분수로 바꾸어 계산합니다. "
            f"${_fm(x)} {_D} {_frac(c, d)} = {_frac(x.numerator, x.denominator)} {_X} {_frac(d, c)} = {target}$입니다."
        )
        return q, exp, str(target)

    def make():
        b, d = random.randint(2, 6), random.choice([2, 3, 4, 5, 6, 8])
        a, c = random.randint(1, b - 1), random.randint(1, d - 1)
        if math.gcd(a, b) != 1 or math.gcd(c, d) != 1:
            return None
        total = Fraction(a, b) + target * Fraction(c, d)
        return (int(total), a, b, c, d) if total.denominator == 1 and 2 <= total <= 40 else None

    total, a, b, c, d = _pick(make)
    left = Fraction(total) - Fraction(a, b)
    q = (
        f"길이가 {total} m인 끈이 있습니다. 이 끈에서 ${_frac(a, b)}$ m를 사용하고, "
        f"남은 끈을 ${_frac(c, d)}$ m씩 자르려고 합니다. 몇 도막으로 자를 수 있습니까?"
    )
    exp = (
        f"남은 끈의 길이는 ${total}-{_frac(a, b)}={_fm(left)}$(m)입니다. "
        f"${_fm(left)} {_D} {_frac(c, d)} = {_frac(left.numerator, left.denominator)} {_X} {_frac(d, c)} = {target}$이므로 "
        f"{target}도막으로 자를 수 있습니다."
    )
    return q, exp, str(target)


def e6_decdiv(target, level):
    """소수의 나눗셈."""
    if level == 1:
        d10 = random.choice([2, 4, 5, 6, 8, 12, 15, 16, 24, 25])
        n10 = target * d10
        q = f"다음을 계산하시오.\n\n$${_dec(n10)} {_D} {_dec(d10)}$$"
        exp = (
            f"나누는 수와 나누어지는 수의 소수점을 오른쪽으로 한 자리씩 옮기면 "
            f"${n10} {_D} {d10}={target}$입니다."
        )
        return q, exp, str(target)
    if level == 2:
        d10 = random.choice([n for n in range(11, 50) if n % 10])
        r = random.randint(1, d10 - 1)
        n10 = target * d10 + r
        q = f"${_dec(n10)} {_D} {_dec(d10)}$의 몫을 자연수 부분까지만 구하시오."
        exp = (
            f"소수점을 오른쪽으로 한 자리씩 옮기면 ${n10} {_D} {d10}={target}\\cdots{r}$입니다. "
            f"따라서 몫을 자연수 부분까지 구하면 ${target}$입니다."
        )
        return q, exp, str(target)
    d10 = random.choice([n for n in range(3, 26) if n % 10])
    a10 = random.randint(5, 40)
    r = random.randint(0, d10 - 1)
    n10 = a10 + target * d10 + r
    q = (
        f"리본이 {_dec(n10)} m 있습니다. 이 중 {_dec(a10)} m는 선물 포장에 쓰고, "
        f"남은 리본을 {_dec(d10)} m씩 잘라 머리핀 장식을 만들려고 합니다. 장식을 몇 개까지 만들 수 있습니까?"
    )
    exp = (
        f"남은 리본은 ${_dec(n10)}-{_dec(a10)}={_dec(n10 - a10)}$(m)입니다. "
        f"소수점을 오른쪽으로 한 자리씩 옮기면 ${n10 - a10} {_D} {d10}={target}\\cdots{r}$이므로 "
        f"장식을 {target}개까지 만들 수 있습니다."
    )
    return q, exp, str(target)


def e6_ratio(target, level):
    """비와 비례식, 비례배분."""
    if level == 1:
        y = random.choice([d for d in _divisors(target) if 2 <= d <= 9])
        x = random.choice([v for v in range(1, 10) if math.gcd(v, y) == 1 and v != y])
        s1 = random.randint(2, 6)
        s2 = target // y
        a, b, c = x * s1, y * s1, x * s2
        if c == a:
            s1 = s1 + 1 if s1 < 6 else s1 - 1
            a, b = x * s1, y * s1
        q = f"비례식 ${a} : {b} = {c} : \\square$에서 □ 안에 알맞은 수를 구하시오."
        exp = (
            f"${a} : {b}$를 가장 간단한 자연수의 비로 나타내면 ${x} : {y}$입니다. "
            f"${c}={x} {_X} {s2}$이므로 □ $={y} {_X} {s2}={target}$입니다."
        )
        return q, exp, str(target)
    if level == 2:
        def make():
            a, b = random.randint(1, 9), random.randint(1, 9)
            if a == b or (target * a) % b:
                return None
            return a, b

        a, b = _pick(make)
        total = target * (a + b) // b
        q = f"사탕 {total}개를 형과 동생이 ${a} : {b}$로 나누어 가지려고 합니다. 동생은 사탕을 몇 개 가지게 됩니까?"
        exp = (
            f"전체를 ${a}+{b}={a + b}$로 나누어 동생이 {b}만큼 가지므로 "
            f"(동생의 사탕)$={total} {_X} {_frac(b, a + b)}={target}$(개)입니다."
        )
        return q, exp, str(target)

    def make():
        p, q_, r, s = (random.randint(2, 9) for _ in range(4))
        if (target * p * r) % (q_ * s) or (target * r) % s:
            return None
        g = target * p * r // (q_ * s)
        return (g, p, q_, r, s) if 2 <= g <= 150 else None

    g, p, q_, r, s = _pick(make)
    mid = g * q_ // p
    q = (
        f"구슬이 가 상자, 나 상자, 다 상자에 들어 있습니다. 가 상자와 나 상자의 구슬 수의 비는 ${p} : {q_}$, "
        f"나 상자와 다 상자의 구슬 수의 비는 ${r} : {s}$입니다. 가 상자에 구슬이 {g}개 들어 있다면 다 상자에는 구슬이 몇 개 들어 있습니까?"
    )
    exp = (
        f"가 : 나 $={p} : {q_}$이므로 나 상자의 구슬은 ${g} {_X} {_frac(q_, p)}={mid}$(개)입니다. "
        f"나 : 다 $={r} : {s}$이므로 다 상자의 구슬은 ${mid} {_X} {_frac(s, r)}={target}$(개)입니다."
    )
    return q, exp, str(target)


def e6_prism(target, level):
    """각기둥과 각뿔."""
    # 종류별: (이름, 각기둥의 개수, 각뿔의 개수)
    kinds = {
        1: ("면의 수", lambda n: n + 2, lambda m: m + 1),
        2: ("꼭짓점의 수", lambda n: 2 * n, lambda m: m + 1),
        3: ("모서리의 수", lambda n: 3 * n, lambda m: 2 * m),
    }
    name, prism, pyramid = kinds[level]
    n, m = _roll(lambda: (random.randint(3, 20), random.randint(3, 20)), lambda c: prism(c[0]) - pyramid(c[1]), target)
    q = f"{n}각기둥의 {name}에서 {m}각뿔의 {name}를 뺀 값은 얼마입니까?"
    exp = (
        f"n각기둥의 면은 (n+2)개, 꼭짓점은 (2×n)개, 모서리는 (3×n)개이고, "
        f"n각뿔의 면과 꼭짓점은 (n+1)개, 모서리는 (2×n)개입니다. "
        f"{n}각기둥은 {prism(n)}개, {m}각뿔은 {pyramid(m)}개이므로 ${prism(n)}-{pyramid(m)}={target}$입니다."
    )
    return q, exp, str(target)


def e6_solid(target, level):
    """직육면체의 부피와 겉넓이."""
    if level == 1:
        a, b = random.randint(3, 12), random.randint(3, 12)
        v = a * b * target
        q = f"가로가 {a} cm, 세로가 {b} cm이고 부피가 {v} cm³인 직육면체의 높이는 몇 cm입니까?"
        exp = f"(부피)=(가로)×(세로)×(높이)이므로 (높이)$={v} {_D} ({a} {_X} {b})={v} {_D} {a * b}={target}$(cm)입니다."
        return q, exp, str(target)
    if level == 2:
        s = 6 * target * target
        q = f"겉넓이가 {s} cm²인 정육면체의 한 모서리의 길이는 몇 cm입니까?"
        exp = (
            f"정육면체의 겉면은 합동인 정사각형 6개이므로 한 면의 넓이는 ${s} {_D} 6={target * target}$(cm²)입니다. "
            f"${target} {_X} {target}={target * target}$이므로 한 모서리의 길이는 ${target}$ cm입니다."
        )
        return q, exp, str(target)

    def make():
        a, b = random.choice([20, 25, 40, 50, 80, 100]), random.choice([20, 25, 40, 50, 80, 100])
        return (a, b) if (a * b * target) % 1000 == 0 else None

    a, b = _pick(make)
    v = a * b * target // 1000
    q = (
        f"가로가 {a} cm, 세로가 {b} cm인 직육면체 모양의 빈 수조에 물을 {v} L 부었습니다. "
        f"물의 높이는 몇 cm입니까? (1 L = 1000 cm³)"
    )
    exp = (
        f"물의 부피는 ${v} {_X} 1000={v * 1000}$(cm³)입니다. "
        f"(물의 높이)=(부피)÷(가로×세로)$={v * 1000} {_D} {a * b}={target}$(cm)입니다."
    )
    return q, exp, str(target)


def e6_circle(target, level):
    """원의 넓이와 둘레 (원주율은 3으로 계산)."""
    if level == 1:
        c = 3 * target
        q = f"원주가 {c} cm인 원의 지름은 몇 cm입니까? (원주율: 3)"
        exp = f"(원주)=(지름)×(원주율)이므로 (지름)$={c} {_D} 3={target}$(cm)입니다."
        return q, exp, str(target)
    if level == 2:
        area = 3 * target * target
        q = f"넓이가 {area} cm²인 원의 반지름은 몇 cm입니까? (원주율: 3)"
        exp = (
            f"(원의 넓이)=(반지름)×(반지름)×(원주율)이므로 (반지름)×(반지름)$={area} {_D} 3={target * target}$입니다. "
            f"${target} {_X} {target}={target * target}$이므로 반지름은 ${target}$ cm입니다."
        )
        return q, exp, str(target)
    big = target + random.randint(1, 10)
    area = 3 * (big * big - target * target)
    q = (
        f"반지름이 {big} cm인 원 안에 크기가 다른 작은 원을 같은 중심으로 그렸더니, 두 원 사이 색칠한 부분의 넓이가 "
        f"{area} cm²였습니다. 작은 원의 반지름은 몇 cm입니까? (원주율: 3)"
    )
    exp = (
        f"큰 원의 넓이는 ${big} {_X} {big} {_X} 3={3 * big * big}$(cm²)이므로 "
        f"작은 원의 넓이는 ${3 * big * big}-{area}={3 * target * target}$(cm²)입니다. "
        f"(반지름)×(반지름)$={3 * target * target} {_D} 3={target * target}$이므로 작은 원의 반지름은 ${target}$ cm입니다."
    )
    return q, exp, str(target)


def e6_cylinder(target, level):
    """원기둥 (원주율은 3으로 계산)."""
    if level == 1:
        q = f"원기둥의 전개도에서 옆면의 가로가 {3 * target} cm입니다. 이 원기둥의 밑면의 지름은 몇 cm입니까? (원주율: 3)"
        exp = (
            f"옆면의 가로는 밑면의 둘레(원주)와 같으므로 (지름)$={3 * target} {_D} 3={target}$(cm)입니다."
        )
        return q, exp, str(target)
    r = random.randint(2, 10)
    if level == 2:
        v = 3 * r * r * target
        q = f"밑면의 반지름이 {r} cm이고 부피가 {v} cm³인 원기둥의 높이는 몇 cm입니까? (원주율: 3)"
        exp = (
            f"(밑면의 넓이)$={r} {_X} {r} {_X} 3={3 * r * r}$(cm²)이고 (부피)=(밑면의 넓이)×(높이)이므로 "
            f"(높이)$={v} {_D} {3 * r * r}={target}$(cm)입니다."
        )
        return q, exp, str(target)
    r = random.randint(2, 8)
    s = 6 * r * (r + target)
    q = f"밑면의 반지름이 {r} cm이고 겉넓이가 {s} cm²인 원기둥의 높이는 몇 cm입니까? (원주율: 3)"
    exp = (
        f"밑면의 넓이는 ${r} {_X} {r} {_X} 3={3 * r * r}$(cm²)이므로 두 밑면의 넓이의 합은 ${3 * r * r * 2}$(cm²)입니다. "
        f"옆면의 넓이는 ${s}-{3 * r * r * 2}={s - 6 * r * r}$(cm²)이고, 옆면의 가로는 원주 ${2 * r} {_X} 3={6 * r}$(cm)이므로 "
        f"(높이)$={s - 6 * r * r} {_D} {6 * r}={target}$(cm)입니다."
    )
    return q, exp, str(target)


def e6_blocks(target, level):
    """쌓기나무."""
    if level == 1:
        count = 2 if target < 3 else 3
        cuts = sorted(random.sample(range(1, target), count - 1))
        parts = sorted([b - a for a, b in zip([0] + cuts, cuts + [target])], reverse=True)
        layers = ", ".join(f"{i + 1}층에 {v}개" for i, v in enumerate(parts))
        q = f"쌓기나무로 만든 모양을 층별로 세었더니 {layers}가 있었습니다. 쌓기나무는 모두 몇 개입니까?"
        exp = f"층마다 쌓기나무의 수를 더하면 ${'+'.join(str(v) for v in parts)}={target}$(개)입니다."
        return q, exp, str(target)
    triples = [
        (a, b, c)
        for a in _divisors(target)
        for b in _divisors(target)
        for c in _divisors(target)
        if a * b * c == target and max(a, b, c) <= 9
    ]
    if level == 2:
        a, b, c = random.choice(triples)
        q = (
            f"쌓기나무를 가로에 {a}개, 세로에 {b}개씩 놓아 1층을 만들고, {c}층으로 쌓아 직육면체 모양을 만들었습니다. "
            f"사용한 쌓기나무는 모두 몇 개입니까?"
        )
        exp = f"한 층에 ${a} {_X} {b}={a * b}$(개)씩 {c}층이므로 ${a * b} {_X} {c}={target}$(개)입니다."
        return q, exp, str(target)
    a, b, c = random.choice(triples)
    a, b, c = a + 2, b + 2, c + 2
    q = (
        f"한 모서리가 1 cm인 쌓기나무를 가로 {a}개, 세로 {b}개, 높이 {c}개로 쌓아 직육면체를 만든 뒤 겉면을 모두 색칠했습니다. "
        f"어느 면에도 색칠되지 않은 쌓기나무는 모두 몇 개입니까?"
    )
    exp = (
        f"겉면에 닿지 않은 쌓기나무는 가로, 세로, 높이가 각각 양쪽으로 1개씩 줄어든 부분입니다. "
        f"가로 {a - 2}개, 세로 {b - 2}개, 높이 {c - 2}개이므로 ${a - 2} {_X} {b - 2} {_X} {c - 2}={target}$(개)입니다."
    )
    return q, exp, str(target)


def e6_graph(target, level):
    """비율그래프와 백분율."""
    if level == 1:
        a = random.choice([a for a in (5, 10, 20, 25, 50, 60, 75) if (100 * target) % a == 0])
        n = 100 * target // a
        q = f"학생 {n}명에게 좋아하는 운동을 조사했더니 축구를 고른 학생이 전체의 {a}%였습니다. 축구를 고른 학생은 몇 명입니까?"
        exp = f"${n} {_X} {_frac(a, 100)}={target}$(명)입니다."
        return q, exp, str(target)
    if level == 2:
        def make():
            d = random.choice([5, 10, 15, 20, 25, 30, 40, 50, 60])
            if (100 * target) % d:
                return None
            n = 100 * target // d
            units = (100 - d) // 5
            cuts = sorted(random.sample(range(1, units), 2))
            a, b, c = 5 * cuts[0], 5 * (cuts[1] - cuts[0]), 5 * (units - cuts[1])
            return (n, a, b, c, d) if all(n * x % 100 == 0 for x in (a, b, c)) else None

        n, a, b, c, d = _pick(make)
        q = (
            f"좋아하는 과일을 조사하여 띠그래프로 나타냈습니다. 조사한 학생은 {n}명이고, 사과가 {a}%, 귤이 {b}%, 포도가 {c}%이고 "
            f"나머지는 바나나입니다. 바나나를 좋아하는 학생은 몇 명입니까?"
        )
        exp = (
            f"바나나의 비율은 ${100}-{a}-{b}-{c}={d}$(%)입니다. "
            f"따라서 바나나를 좋아하는 학생은 ${n} {_X} {_frac(d, 100)}={target}$(명)입니다."
        )
        return q, exp, str(target)

    def make():
        a = random.choice([10, 20, 25, 30, 40, 50])
        p, q_ = random.randint(1, 9), random.randint(1, 9)
        if p == q_ or math.gcd(p, q_) != 1 or (target * (p + q_)) % q_:
            return None
        rest_count = target * (p + q_) // q_
        if (rest_count * 100) % (100 - a):
            return None
        n = rest_count * 100 // (100 - a)
        return (n, a, p, q_) if 10 <= n <= 500 and (n * a) % 100 == 0 else None

    n, a, p, q_ = _pick(make)
    rest = n * (100 - a) // 100
    q = (
        f"마을 주민 {n}명에게 가장 좋아하는 계절을 조사했더니 봄을 고른 사람이 {a}%였고, "
        f"나머지 사람들은 여름과 가을을 ${p} : {q_}$의 비로 골랐습니다. 가을을 고른 사람은 몇 명입니까?"
    )
    exp = (
        f"봄을 고르지 않은 사람은 전체의 ${100 - a}$%이므로 ${n} {_X} {_frac(100 - a, 100)}={rest}$(명)입니다. "
        f"이 중 가을을 고른 사람은 ${rest} {_X} {_frac(q_, p + q_)}={target}$(명)입니다."
    )
    return q, exp, str(target)


UNIT_FAMILIES = {
    5: [
        ("자연수의 혼합 계산", [e5_calc]),
        ("약수와 배수", [e5_multiple]),
        ("규칙과 대응", [e5_rule]),
        ("약분과 통분 · 분수의 덧셈과 뺄셈", [e5_fraction]),
        ("다각형의 둘레와 넓이", [e5_area]),
        ("수의 범위와 어림하기", [e5_range]),
        ("분수의 곱셈", [e5_fracmul]),
        ("평균", [e5_avg]),
        ("직육면체", [e5_cuboid]),
    ],
    6: [
        ("분수의 나눗셈", [e6_fracdiv]),
        ("소수의 나눗셈", [e6_decdiv]),
        ("비와 비례식", [e6_ratio]),
        ("각기둥과 각뿔", [e6_prism]),
        ("직육면체의 부피와 겉넓이", [e6_solid]),
        ("원의 넓이", [e6_circle]),
        ("원기둥", [e6_cylinder]),
        ("쌓기나무", [e6_blocks]),
        ("비율그래프", [e6_graph]),
    ],
}
GRADE_TITLES = {5: "초등학교 5학년 수학", 6: "초등학교 6학년 수학"}


# ==========================================
# 문제 만들기 (문제 선택 화면의 3문제 + 따로 떨어진 빨간 '매우 어려움' 문제)
#  - 문제 묶음은 (문제, 풀이, 정답) 이다. 일반 문제 3개는 서로 다른 단원에서 뽑고, 정답은 15·9·2 를 섞어서 쓴다.
#    '앞에서 봤던 정답을 큰 순서대로' 이어 쓴 1592 가 타임머신 암호이기 때문이다.
#  - '매우 어려움' 문제는 학년의 서로 다른 세 단원에서 난이도 '상' 문제를 하나씩 뽑아 (가)(나)(다)로 내고,
#    답이 차례로 15, 9, 2 이므로 "세 답을 이어 쓴 수"(=1592)를 묻는다. 이 문제만 풀어도 타임머신으로 갈 수 있다.
#  - 한 번 정한 '매우 어려움' 문제는 학년마다 세션 동안 그대로 쓰고, 학년 버튼을 다시 누르면 새로 정한다.
# ==========================================
FAST_PASS_INDEX = 3
FAST_PASS_ANSWER = 1592
FAST_PASS_TARGETS = (15, 9, 2)


def generate_problems(grade, difficulty=1):
    """학년과 난이도에 맞는 문제 3개: (제목, [(문제, 풀이, 정답), ...])."""
    grade = grade if grade in GRADE_TITLES else 6
    title = f"[ {GRADE_TITLES[grade]} · 난이도 {DIFFICULTY_LABELS[difficulty]} ]"
    targets = [15, 9, 2]
    random.shuffle(targets)
    problems = []
    for target, (unit, families) in zip(targets, random.sample(UNIT_FAMILIES[grade], 3)):
        question, explanation, answer = random.choice(families)(target, difficulty)
        problems.append((question, f"*({unit} 단원)*\n\n{explanation}", answer))
    return title, problems


def make_fast_pass(grade):
    """'매우 어려움' 문제 하나: (제목, (문제, 풀이, 정답))."""
    grade = grade if grade in GRADE_TITLES else 6
    units = random.sample(UNIT_FAMILIES[grade], 3)
    labels = ("(가)", "(나)", "(다)")
    parts = [random.choice(families)(target, 3) for (_, families), target in zip(units, FAST_PASS_TARGETS)]
    questions = [f"**{label}**\n\n{q}" for label, (q, _, _) in zip(labels, parts)]
    question = (
        "다음 세 문제 (가), (나), (다)의 답을 각각 구한 뒤, 차례로 이어 쓴 수를 구하시오.\n\n"
        "(예: 세 문제의 답이 7, 12, 3이면 7123)\n\n" + "\n\n".join(questions)
    )
    explanations = [
        f"**{label}** *({unit} 단원)*\n\n{e}" for label, (unit, _), (_, e, _) in zip(labels, units, parts)
    ]
    a, b, c = FAST_PASS_TARGETS
    explanation = "\n\n".join(explanations) + (
        f"\n\n(가), (나), (다)의 답이 차례로 ${a}$, ${b}$, ${c}$이므로 이어 쓰면 ${FAST_PASS_ANSWER}$입니다."
    )
    return f"[ {GRADE_TITLES[grade]} · 매우 어려움 ]", (question, explanation, str(FAST_PASS_ANSWER))


def fast_pass_for_grade():
    """지금 고른 학년의 '매우 어려움' 문제: (제목, (문제, 풀이, 정답)). 학년을 모르면 6학년으로 본다.
    한 번 정한 문제는 학년마다 세션 동안 그대로 쓴다. (화면이 다시 그려질 때마다 문제가 바뀌면 안 된다.)"""
    grade = st.session_state.get("grade")
    grade = grade if grade in GRADE_TITLES else 6
    cache = st.session_state.setdefault("fast_pass_cache", {})
    if grade not in cache:
        cache[grade] = make_fast_pass(grade)
    return cache[grade]


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
# 정답 입력칸에 이전에 쓴 답이 자동 완성으로 뜨지 않게 한다.
disable_answer_autofill()
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
            font-size: 22px !important;
            font-weight: 900 !important;
            /* 궁서체·굴림체는 굵은 글자꼴이 없어 bold 만으로는 티가 안 나므로, 같은 색의 가는 테두리를 덧대 굵게 보이게 한다. */
            -webkit-text-stroke: 0.6px #1a1a1a;
            letter-spacing: 1px;
            text-align: center;
            word-break: keep-all;
        }}
        div.stButton > button, div[data-testid="stButton"] > button {{
            background-color: #ffffff !important;
            color: #000000 !important;
            font-weight: bold !important;
            border: 1px solid #cccccc !important;
            font-size: 17px !important;
            padding: 10px 24px !important;
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
            time.sleep(TYPING_DELAY_SECONDS)
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

            # 자동 완성을 꺼서 이전에 쓴 답이 키보드 위쪽에 뜨지 않게 한다. (나머지 설정은 disable_answer_autofill)
            tm_answer = st.text_input(
                "정답 입력",
                autocomplete="off",
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
            font-size: 22px !important;
            font-weight: 900 !important;
            /* 궁서체·굴림체는 굵은 글자꼴이 없어 bold 만으로는 티가 안 나므로, 같은 색의 가는 테두리를 덧대 굵게 보이게 한다. */
            -webkit-text-stroke: 0.6px #000000;
            letter-spacing: 1px;
            text-align: center;
            word-break: keep-all;
        }}
        div.stButton > button, div[data-testid="stButton"] > button {{
            background-color: #ffffff !important;
            color: #000000 !important;
            font-weight: bold !important;
            border: 1px solid #cccccc !important;
            font-size: 17px !important;
            padding: 10px 24px !important;
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
            time.sleep(TYPING_DELAY_SECONDS)
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
        # 화면에 보이는 스톱워치 합계에는 오답 벌칙 시간이 더해져 있으므로, 퍼즐 크기도 같은 값으로 정한다.
        total_ms = elapsed_ms + st.session_state.get("wrong_answer_count", 0) * WRONG_ANSWER_PENALTY_SECONDS * 1000
        grid_size = pipe_puzzle_grid_size(st.session_state.get("difficulty", 1), total_ms)
        st.session_state.prison_key_html = PRISON_KEY_PUZZLE_HTML.replace(
            "__ELAPSED_BEFORE_MS__", str(elapsed_ms)
        ).replace("__PRISON_CLUE__", html_escape(prison_clue_text())).replace("__GRID_SIZE__", str(grid_size))
    st.iframe(st.session_state.prison_key_html, height=900)

# --- 비밀의 방: 어떤 버튼이든 500분의 1 확률로 누르면 도착하는 화면 ---
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
                축하합니다. 당신은 1 / 500 (0.2%)을 뚫었습니다.<br>
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

        cols = st.columns(2)
        for col, (label, grade) in zip(cols, [("초5 문제", 5), ("초6 문제", 6)]):
            if lucky_button(label, use_container_width=True, dg=col, key=f"grade_{grade}"):
                st.session_state.grade = grade
                st.session_state.pop("fast_pass_cache", None)
                st.session_state.title, st.session_state.problems = (
                    generate_problems(grade, st.session_state.difficulty)
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

        # 문제 3개 + 따로 떨어진 빨간 '매우 어려움' 문제(고른 학년의 문제) 1개. (이 문제의 정답은 타임머신 암호 1592 다.)
        fast_title, fast_problem = fast_pass_for_grade()
        entries = list(st.session_state.problems) + [fast_problem]

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
        for i in range(3):
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

        # 문제 3개를 모두 풀었거나, 매우 어려움 문제를 풀었으면 타임머신으로 갈 수 있다.
        solved = st.session_state.solved_indices
        if FAST_PASS_INDEX in solved or {0, 1, 2} <= solved:
            st.write("")
            st.write("")
            st.markdown("---")
            if lucky_button(
                "🚀 타임머신 작동자에게 가기", type="primary", use_container_width=True
            ):
                st.session_state.step = "timemachine"
                st.session_state.timemachine_success = False
                st.rerun()

    # --- 화면 3: 문제 풀이 ---
    elif st.session_state.step == "solve":
        current_q, current_exp, current_ans = st.session_state.current_problem[:3]

        # 제목 · 구분선 · 문제를 흰 상자 하나에 담는다.
        readable_markdown(
            f"<h3>{html_escape(st.session_state.get('current_title', st.session_state.title))}</h3>\n\n<hr>\n\n{current_q}"
        )
        st.write("")

        # 자동 완성을 꺼서 이전에 쓴 답이 키보드 위쪽에 뜨지 않게 한다. (나머지 설정은 disable_answer_autofill)
        user_answer = st.text_input(
            "정답을 입력하세요 (숫자만 입력):",
            autocomplete="off",
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
                    st.error(f"아쉽지만 정답이 아니에요. 다시 풀어 보세요! (+{penalty_label()})")

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
