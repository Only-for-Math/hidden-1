import math
import os
import random
import threading
import time
from fractions import Fraction
from html import escape as html_escape
from pathlib import Path
import streamlit as st

# 타임머신 카운트업 애니메이션 재생 시간(ms)
TIMEMACHINE_ANIM_DURATION_MS = 4500

# 앱 전체에서 공통으로 사용할 폰트 (굴림체)
APP_FONT_FAMILY = "'굴림', Gulim, sans-serif"

# ==========================================
# 방문자 수 기록 (외부 TXT 파일에 저장)
#  - 누군가 앱에 접속할 때마다(새 세션이 열릴 때마다) 방문자 수를 1 늘려서 TXT 파일에 저장한다.
#  - 같은 사람이 버튼을 눌러 스크립트가 다시 실행돼도 늘지 않는다. (세션당 딱 한 번)
#    페이지를 새로고침하거나 새 탭으로 열면 새 세션이라 1 늘어난다.
#  - 저장 파일은 이 파일 옆의 visitor_count.txt 이고, 안에는 방문자 수 숫자 하나만 들어 있다.
#    메모장으로 열어 숫자를 확인하거나 고칠 수 있다. (예: 0 으로 바꾸면 처음부터 다시 센다)
#    서버를 껐다 켜도 이어서 센다. 파일 위치는 환경변수 VISITOR_COUNT_FILE 로 바꿀 수 있다.
#  - 인트로 화면 아래에 'N번째 방문자'로 보여 준다. 숨기려면 SHOW_VISITOR_COUNT 를 False 로 바꾼다.
#  - 접속이 동시에 몰려도 숫자가 꼬이지 않게 잠금(lock)을 걸고, 쓰는 도중 꺼져도 파일이 깨지지 않게
#    임시 파일에 쓴 뒤 바꿔치기(os.replace)한다. 저장에 실패해도(읽기 전용 폴더 등) 앱은 그대로 동작한다.
#  - 주의: Streamlit Community Cloud 처럼 재배포·재시작 때 파일이 초기화되는 곳에서는 기록도 초기화된다.
# ==========================================
SHOW_VISITOR_COUNT = True
VISITOR_COUNT_FILE = Path(
    os.environ.get("VISITOR_COUNT_FILE") or Path(__file__).resolve().parent / "visitor_count.txt"
)
_visitor_lock = threading.Lock()


def _load_visitor_total():
    """TXT 파일에 적힌 방문자 수를 읽는다. 파일이 없거나 비어 있으면 0 에서 시작한다.

    숫자가 아닌 내용이 들어 있으면(잘못 고쳤을 때 등) 기록을 덮어써서 날리지 않도록
    옆에 .broken 으로 보관해 두고 0 에서 새로 시작한다.
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
    except (ValueError, OSError):
        try:
            os.replace(VISITOR_COUNT_FILE, f"{VISITOR_COUNT_FILE}.broken")
        except OSError:
            pass
        return 0


def record_visit():
    """방문자 수를 1 늘려 TXT 파일에 저장하고, 이번 방문이 몇 번째인지 돌려준다. 저장에 실패하면 None."""
    with _visitor_lock:
        total = _load_visitor_total() + 1
        tmp_path = f"{VISITOR_COUNT_FILE}.tmp"
        try:
            VISITOR_COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(f"{total}\n")
            os.replace(tmp_path, VISITOR_COUNT_FILE)
        except OSError as e:
            print(f"[방문자 수] 저장하지 못했습니다: {e}")
            return None
        return total


# ==========================================
# 비밀의 방
#  - 앱 안의 어떤 버튼이든(파이썬 화면의 모든 버튼 + 감옥 열쇠 퍼즐 화면의 모든 버튼)
#    누를 때마다 딱 한 번씩 25만분의 1 확률로 주사위를 굴린다.
#  - 당첨되면 그 버튼의 원래 동작은 실행되지 않고 곧바로 비밀의 방으로 이동한다.
#  - 동작을 확인해 보고 싶으면 SECRET_ROOM_ODDS 를 1 로 바꾸면 항상 이동한다.
#    (감옥 퍼즐 화면 쪽 확률은 아래 PRISON_KEY_PUZZLE_HTML 안의 SECRET_ROOM_ODDS)
# ==========================================
SECRET_ROOM_ODDS = 250_000
_secret_rng = random.SystemRandom()


def go_secret_room():
    """현재 화면을 기억해 두고 비밀의 방 화면으로 넘어간다."""
    if st.session_state.step != "secret_room":
        st.session_state.secret_room_prev_step = st.session_state.step
        st.session_state.step = "secret_room"
    st.rerun()


def lucky_button(label, *args, dg=None, **kwargs):
    """st.button과 똑같이 쓰되, 클릭될 때마다 25만분의 1 확률로 비밀의 방으로 보낸다.

    dg에 st.columns의 칼럼 등을 넘기면 그 안에 버튼이 그려진다.
    """
    target = dg if dg is not None else st
    clicked = target.button(label, *args, **kwargs)
    if clicked:
        blue_watch_turn = count_click_and_check_blue_watch_clue()
        if _secret_rng.randrange(SECRET_ROOM_ODDS) == 0:
            go_secret_room()
        if blue_watch_turn:
            go_blue_watch_clue()
    return clicked


# 인트로·도착 화면의 오른쪽 아래 '다음으로' 버튼을 왼쪽으로 조금 옮긴다.
# 폰처럼 좁은 화면(640px 이하)에서는 칼럼이 세로로 쌓여 버튼이 전체 너비를 쓰므로 옮기지 않는다.
NEXT_BUTTON_SHIFT_CSS = """
@media (min-width: 641px) {
    div[data-testid="stButton"] {
        transform: translateX(-1.5rem);
    }
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
# 스토커 단서 화면 (문제 선택 화면에 숨겨 둔 이벤트)
#  - 문제 선택 화면의 버튼 3개 중 하나가 무작위로 정해진다. (한 판에 한 번만 뽑는다)
#  - 그 버튼을 처음 누르는 순간 반드시 검은 화면으로 넘어가 단서 문장을 보여 주고,
#    STALKER_CLUE_SECONDS 초가 지나면 문제 선택 화면으로 저절로 돌아온다.
#    (이때 버튼의 원래 동작은 실행되지 않는다. 돌아와서 다시 누르면 평소처럼 문제로 들어간다.)
#    문장 아래에는 남은 시간이 5, 4, 3, 2, 1 로 작게 줄어든다.
#  - 문제 3개를 모두 풀어야 타임머신에 갈 수 있어서 버튼 3개를 모두 누르게 되므로,
#    단서는 한 판에 정확히 한 번, 반드시 나온다.
#  - 이 검은 단서 화면은 파란색 시계 단서도 함께 쓰므로, 표시 시간도 같다.
# ==========================================
STALKER_CLUE_TEXT = "스토커는 녹색 장갑을 끼고 있었다"
STALKER_CLUE_SECONDS = 5


def reset_stalker_clue():
    """새 문제 세트를 시작할 때마다 단서 버튼을 새로 뽑도록 상태를 비운다."""
    st.session_state.stalker_clue_shown = False
    st.session_state.stalker_clue_button_idx = None


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
    """검은 단서 화면으로 넘어간다. 한 판에 한 번만 보여 준다."""
    st.session_state.stalker_clue_shown = True
    go_clue(STALKER_CLUE_TEXT, "select_problem")


# ==========================================
# 파란색 시계 단서 (앱 전체에서 딱 한 번, 무작위 버튼에 숨겨 둔 이벤트)
#  - 파이썬 화면의 버튼(lucky_button)을 누를 때마다 클릭 횟수를 센다.
#  - 세션을 시작할 때 1 ~ BLUE_WATCH_CLUE_MAX_CLICK 사이에서 '몇 번째 클릭'일지 미리 뽑아 두고,
#    그 번째 버튼을 누르는 순간 검은 화면에 단서 문장을 보여 준다.
#    (이때 버튼의 원래 동작은 실행되지 않는다. 단서가 끝나면 누르던 화면으로 돌아온다.)
#  - 한 번 보여 주면 다시는 나오지 않는다. 새 문제 세트를 시작해도 초기화하지 않는다.
#  - 시작 화면(인트로)과 난이도 선택 화면은 단서가 나오지 않는 구간이다. 이 두 화면의 버튼은
#    클릭 횟수에도 세지 않는다. (BLUE_WATCH_CLUE_EXEMPT_STEPS)
#  - 그 구간을 뺀 파이썬 버튼만 해도 감옥 열쇠 퍼즐까지 가려면 최소 13번은 눌러야 하므로,
#    최대 클릭 수를 그보다 작게 두면 단서는 반드시 한 번 나온다.
#  - 비밀의 방이 그 클릭을 먼저 가져가도 단서를 놓치지 않도록, '정확히 N번째'가 아니라
#    'N번째 이후 첫 클릭'에 보여 준다.
# ==========================================
BLUE_WATCH_CLUE_TEXT = "스토커는 파란색 계열의 시계를 차고 있었다"
BLUE_WATCH_CLUE_MAX_CLICK = 12
BLUE_WATCH_CLUE_EXEMPT_STEPS = ("intro", "select_difficulty")


def count_click_and_check_blue_watch_clue():
    """버튼 클릭을 한 번 세고, 이번 클릭이 파란색 시계 단서를 보여 줄 차례면 True.

    시작~난이도 선택 구간의 클릭은 세지도 않고 단서도 띄우지 않는다.
    """
    if st.session_state.step in BLUE_WATCH_CLUE_EXEMPT_STEPS:
        return False
    st.session_state.button_click_count += 1
    return (
        not st.session_state.blue_watch_clue_shown
        and st.session_state.button_click_count
        >= st.session_state.blue_watch_clue_target
    )


def go_blue_watch_clue():
    """파란색 시계 단서 화면으로 넘어간다. 앱 전체에서 한 번만 보여 준다."""
    st.session_state.blue_watch_clue_shown = True
    go_clue(BLUE_WATCH_CLUE_TEXT, st.session_state.step)


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

/* 개발자 옵션 체크박스: 글씨 뒤에 흰색 상자 */
div[data-testid="stCheckbox"] {
    width: fit-content !important;
    max-width: 100%;
    box-sizing: border-box;
    padding: 8px 16px !important;
    background: rgba(255, 255, 255, 0.95) !important;
    border: 1px solid rgba(0, 0, 0, 0.12);
    border-radius: 12px;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.28);
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
  #prisonView{ position:fixed; inset:0; overflow:hidden; display:flex; align-items:center; justify-content:center; }
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
    position:relative; z-index:2; width:100%; max-width:380px;
    display:flex; flex-direction:column; align-items:center; gap:26px;
    padding:90px 20px 28px;
  }
  .prison-title{ font-size:1.7rem; letter-spacing:.02em; color:var(--gold-bright); text-shadow:0 3px 10px rgba(0,0,0,.7); text-align:center; }
  .prison-sub{ font-size:.8rem; color:#c9a877; text-align:center; letter-spacing:.03em; }

  .key-rack{ display:flex; flex-direction:column; gap:10px; width:100%; }
  .key-btn{
    position:relative; width:100%; height:56px; padding:0 14px;
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
    position:fixed; top:18px; left:50%; transform:translateX(-50%);
    z-index:10; padding:11px 20px; border-radius:10px; font-size:.85rem;
    display:inline-flex; align-items:center; gap:8px;
  }
  .go-game-btn:hover{ transform:translateX(-50%) translateY(-2px); }
  .go-game-btn:active{ transform:translateX(-50%) translateY(1px); }

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
  .suspects-title{
    position:fixed; top:28px; left:50%; transform:translateX(-50%);
    z-index:5; width:100%; max-width:500px; padding:0 20px;
    font-size:1.5rem; line-height:1.4; letter-spacing:.02em; color:var(--gold-bright);
    text-shadow:0 3px 10px rgba(0,0,0,.7); text-align:center;
  }
  .suspects-content{
    position:relative; z-index:2; width:100%; max-width:500px;
    display:flex; flex-direction:column; align-items:center;
    padding:32px 20px;
  }
  /* 목격자 진술: 스토커의 생김새(장갑 색 / 연 손 / 시계 색)를 알려주는 단서 */
  .clue-box{
    width:100%; text-align:left; margin-bottom:20px; box-sizing:border-box;
    background:rgba(8,5,3,.72);
    border:1px solid rgba(212,175,55,.5); border-radius:14px;
    padding:16px 18px; box-shadow:inset 0 0 14px rgba(0,0,0,.5), 0 6px 18px rgba(0,0,0,.35);
  }
  .clue-heading{
    display:flex; align-items:center; gap:7px;
    font-size:.8rem; letter-spacing:.12em; color:var(--gold-bright);
    font-weight:800; margin-bottom:12px; text-transform:uppercase;
  }
  .clue-list{ display:flex; flex-direction:column; gap:11px; }
  /* 한 줄 = [단서 번호] [아이콘] [문장]. 줄바꿈(wrap)을 막아서 좁은 화면에서도 문장이 아래 줄로 떨어지지 않고,
     문장 칸(마지막 span)만 남은 폭 안에서 단어 단위로 줄바꿈된다. 번호·아이콘은 첫 줄에 맞춰 위쪽 정렬. */
  .clue-item{
    display:flex; align-items:flex-start; gap:8px;
    font-size:.94rem; line-height:1.5; color:var(--parchment);
  }
  .clue-item > span:last-child{ flex:1; min-width:0; word-break:keep-all; overflow-wrap:break-word; text-wrap:balance; }
  .clue-item > span[aria-hidden="true"]{ flex:none; }
  .clue-num{
    flex:none; display:inline-flex; align-items:center; justify-content:center;
    min-width:46px; padding:2px 8px; margin-top:1px; border-radius:8px;
    background:rgba(212,175,55,.18); border:1px solid rgba(212,175,55,.5);
    font-size:.72rem; font-weight:800; letter-spacing:.02em; color:var(--gold-bright); white-space:nowrap;
  }
  .clue-item b{
    color:#1a0d08; font-weight:800; padding:1px 8px; border-radius:6px; white-space:nowrap;
    background:linear-gradient(180deg,#fce080 0%,#d4af37 100%);
    box-shadow:0 1px 3px rgba(0,0,0,.4);
  }
  /* 폰처럼 좁은 화면: 상자 안쪽 여백과 번호 크기를 줄여서 문장 칸을 넓힌다. */
  @media (max-width:400px){
    .clue-box{ padding:14px 12px; }
    .clue-item{ gap:6px; font-size:.88rem; }
    .clue-num{ min-width:40px; padding:2px 6px; font-size:.68rem; }
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

  /* ---------- 비밀의 방 (아무 버튼이든 25만분의 1 확률로 열린다) ---------- */
  #secretRoom{
    position:fixed; inset:0; z-index:2000;
    display:flex; flex-direction:column; align-items:center; justify-content:center; gap:18px;
    padding:24px; text-align:center; color:var(--parchment);
    background:radial-gradient(ellipse at 50% 30%, #4a2a12 0%, #1a0e07 55%, #0a0604 100%);
  }
  #secretRoom .sr-title{ font-size:2rem; letter-spacing:.2em; color:var(--gold-bright); text-shadow:0 0 22px rgba(252,224,128,.55); }
  #secretRoom .sr-text{ font-size:.95rem; line-height:1.8; word-break:keep-all; }

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
    <div>
      <div class="prison-title cinzel-font">탈옥의 열쇠</div>
      <div class="prison-sub" id="prisonSub">일곱 개의 열쇠 중 자물쇠를 여는 것은 단 하나뿐이다</div>
    </div>
    <div class="key-rack" id="keyRack"></div>
  </div>

  <button id="btnGoGame" class="btn-gold go-game-btn">
    <span aria-hidden="true">🧩</span> 게임 하러 가기
  </button>
</div>

<!-- ============================================================ -->
<!-- 용의자 화면 (6번 열쇠로 자물쇠를 풀고 "계속하기"를 누르면 등장)        -->
<!-- ============================================================ -->
<div id="suspectsView" hidden>
  <div class="prison-bg"></div>
  <div class="prison-vignette"></div>
  <div class="suspects-title cinzel-font">용의자 3명 중<br>스토커를 찾아라</div>
  <div class="suspects-content">
    <div class="clue-box">
      <div class="clue-heading"><span aria-hidden="true">🔍</span> 목격자 진술</div>
      <div class="clue-list">
        <div class="clue-item"><span class="clue-num">단서①</span><span aria-hidden="true">🧤</span><span><b>녹색</b> 장갑을 끼고 있었다.</span></div>
        <div class="clue-item"><span class="clue-num">단서②</span><span aria-hidden="true">✋</span><span><b>왼손</b>으로 감옥 문을 열었다.</span></div>
        <div class="clue-item"><span class="clue-num">단서③</span><span aria-hidden="true">⌚</span><span><b>파란색 계열</b>의 시계를 차고 있었다.</span></div>
      </div>
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
<!-- 마지막 화면 ("다음으로"를 누르면 등장): 처음부터 지금까지 걸린 시간                  -->
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
  <div class="sr-title cinzel-font">비밀의 방</div>
  <div class="sr-text">방금 누른 버튼이 25만분의 1의 확률을 뚫었다.<br>이곳에 도착한 것은 아주 드문 일이다.</div>
  <button id="btnSecretBack" class="btn-gold px-8 py-3 rounded-xl font-bold text-sm">돌아가기</button>
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
          showToast('아직 열쇠를 시도할 수 없다. 먼저 퍼즐을 풀어라', 'fail');
          audio.playBuzz();
          return;
        }
        if(i === 6){ playUnlockCelebration(); }
        else { showToast('이 열쇠가 아닌 듯 하다..', 'fail'); audio.playBuzz(); }
      });
      rack.appendChild(btn);
    }
    refreshKeyLockUI();
  }

  function refreshKeyLockUI(){
    const rack = document.getElementById('keyRack');
    if(!rack) return;
    rack.classList.toggle('locked', !keysUnlocked);
    rack.querySelectorAll('.key-btn').forEach(btn=> btn.setAttribute('aria-disabled', String(!keysUnlocked)));
    const sub = document.getElementById('prisonSub');
    if(sub){
      sub.textContent = keysUnlocked
        ? '일곱 개의 열쇠 중 자물쇠를 여는 것은 단 하나뿐이다'
        : '열쇠는 아직 잠겨 있다 — 먼저 퍼즐을 풀어 단서를 찾아라';
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
          showToast('아니다... 다른 사람이다', 'fail');
          audio.playBuzz();
        }
      });
      row.appendChild(btn);
    });
  }

  /* ========================================================
     화면 전환
  ======================================================== */
  // st.iframe은 단방향 임베드지만, srcdoc iframe은 부모 문서와 같은 출처를
  // 공유하므로 window.parent.document에 직접 접근해 안내문 블록을 여닫을 수 있다.
  // (Streamlit 밖의 아티팩트 미리보기 등 parent에 해당 id가 없는 환경에서는
  //  querySelector가 그냥 null을 반환하므로 조용히 아무 일도 하지 않는다.)
  function syncParentIntroVisibility(name){
    try{
      const parentDoc = window.parent && window.parent.document;
      if(!parentDoc) return;
      const introEl = parentDoc.getElementById('prisonIntroBlock');
      if(introEl) introEl.style.display = (name === 'suspects' || name === 'found' || name === 'finish') ? 'none' : '';
    }catch(e){ /* 크로스 오리진 등으로 접근이 막히면 무시 */ }
  }

  // 스토커를 찾은 화면은 '우측 하단' 버튼이 실제로 보이는 화면 아래쪽에 오도록,
  // 이 iframe 의 높이를 바깥 페이지에서 보이는 높이에 맞춘다. (그대로 두면 820px 고정이라
  // 낮은 폰 화면에서는 버튼이 화면 아래로 밀려 스크롤해야 보인다.)
  function fitFrameToParentViewport(){
    try{
      const fr = window.frameElement, pw = window.parent;
      if(!fr || !pw) return;
      // 앞 화면에서 스크롤해 둔 상태라면 iframe 윗부분이 화면 밖에 있으므로, 바깥 페이지를 맨 위로 올린 뒤 잰다.
      const main = pw.document.querySelector('[data-testid="stMain"]');
      if(main) main.scrollTop = 0;
      pw.scrollTo(0, 0);
      const top = Math.max(0, fr.getBoundingClientRect().top);
      const h = Math.round(pw.innerHeight - top - 8);
      fr.style.height = Math.max(420, Math.min(820, h)) + 'px';
    }catch(e){ /* 접근이 막히면 기본 높이(820px) 그대로 둔다 */ }
  }
  window.addEventListener('resize', ()=>{ if(currentView === 'found' || currentView === 'finish') fitFrameToParentViewport(); });

  /* ========================================================
     스톱워치 (화면에는 보이지 않는다)
     처음 "다음으로"를 누른 때부터 마지막 "다음으로"를 누를 때까지 걸린 시간을 잰다.
     시작은 서버(파이썬)가 기록하고, 이 화면을 만들 때까지 흐른 시간(ELAPSED_BEFORE_MS)을 넘겨 준다.
     (서버 시계와 접속한 기기의 시계를 직접 비교하지 않고 '흐른 시간'만 더하므로 시계가 달라도 맞다.)
  ======================================================== */
  const ELAPSED_BEFORE_MS = Math.max(0, Number("__ELAPSED_BEFORE_MS__") || 0);
  const FRAME_OPENED_AT = Date.now();
  function elapsedMs(){ return ELAPSED_BEFORE_MS + (Date.now() - FRAME_OPENED_AT); }
  function formatElapsed(ms){
    const total = Math.round(ms / 1000);
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
    syncParentIntroVisibility(name);
    if(name === 'found' || name === 'finish') fitFrameToParentViewport(); // 안내문을 숨긴 뒤(위치가 바뀐 뒤)에 맞춘다
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
     비밀의 방: 이 화면의 어떤 버튼이든 누를 때마다 25만분의 1 확률로
     원래 동작 대신 비밀의 방이 열린다. (1 로 바꾸면 항상 열린다)
  ======================================================== */
  const SECRET_ROOM_ODDS = 250000;
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
    document.getElementById('btnSecretBack').addEventListener('click', ()=>{ room.hidden = true; });
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
      // 마지막 "다음으로": 처음부터 지금까지 걸린 시간을 알려 주고 끝낸다.
      document.getElementById('finishTime').textContent = `축하합니다. 당신은 ${formatElapsed(elapsedMs())} 걸렸습니다.`;
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
          타임머신 가동 중...
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
# [중1]
# ==========================================
def g1_linear(target):
    a = random.choice([2, 3, 4])
    b = random.randint(1, 5)
    d = random.choice([1, -1, -2])
    c = random.randint(-5, 5)
    e = a * target - a * b + c - d * target

    b_str = f"- {b}" if b > 0 else f"+ {abs(b)}"
    c_str = f"+ {c}" if c > 0 else (f"- {abs(c)}" if c < 0 else "")
    e_str = f"+ {e}" if e > 0 else (f"- {abs(e)}" if e < 0 else "")
    d_str = f"{d}x" if abs(d) != 1 else ("x" if d == 1 else "-x")

    q = f"다음 방정식의 해 $x$를 구하시오.\n\n$${a}(x {b_str}) {c_str} = {d_str} {e_str}$$"
    exp = f"괄호를 풀고 식을 정리하면:\n\n$${a}x - {a*b} {c_str} = {d_str} {e_str}$$\n\n$${a-d}x = {e + a*b - c}$$\n\n따라서 $x={target}$"
    return q, exp, str(target)


def g1_expr(target):
    x_val = random.choice([-3, -2, 2, 3])
    a = random.choice([2, 3])
    b = random.randint(1, 4)
    c = random.choice([2, 4])

    val_in = a * (x_val + b) - c * (x_val - 1)
    k = target - val_in

    k_str = f"+ {k}" if k > 0 else (f"- {abs(k)}" if k < 0 else "")
    b_str = f"+ {b}" if b > 0 else f"- {abs(b)}"

    q = f"$x={x_val}$ 일 때, 다음 식의 값을 구하시오.\n\n$${a}(x {b_str}) - {c}(x - 1) {k_str}$$"
    exp = f"식의 $x$ 자리에 ${x_val}$를 대입하면:\n\n$${a}\\times({x_val} {b_str}) - {c}\\times({x_val} - 1) {k_str}$$\n\n$$= {a}\\times({x_val + b}) - {c}\\times({x_val - 1}) {k_str} = {target}$$"
    return q, exp, str(target)


def g1_prop(target):
    while True:
        a = random.randint(-5, 5)
        b = random.choice([2, 3, 4])
        d = random.choice([3, 5])
        # 비례식에 0 이 들어가지 않도록(target + a != 0) 한다.
        if ((target + a) * d) % b == 0 and target + a != 0:
            break
    c = ((target + a) * d) // b

    a_str = f"+ {a}" if a > 0 else (f"- {abs(a)}" if a < 0 else "")

    q = f"다음 비례식을 만족하는 $x$의 값을 구하시오.\n\n$$(x {a_str}) : {b} = {c} : {d}$$"
    exp = f"내항의 곱과 외항의 곱은 같으므로:\n\n$${d}(x {a_str}) = {b} \\times {c}$$\n\n$${d}x {f'+ {d*a}' if a>0 else (f'- {abs(d*a)}' if a<0 else '')} = {b*c}$$\n\n따라서 $x={target}$"
    return q, exp, str(target)


def g1_stat(target):
    a = random.randint(1, 15)
    b = random.randint(1, 15)
    c = random.randint(1, 15)
    total = a + b + c + target
    while total % 4 != 0:
        c += 1
        total = a + b + c + target
    avg = total // 4

    q = f"네 수 ${a}, {b}, {c}, x$ 의 평균이 ${avg}$일 때, $x$의 값을 구하시오."
    exp = f"평균을 구하는 식을 세우면:\n\n$$\\frac{{{a} + {b} + {c} + x}}{{4}} = {avg}$$\n\n$${a+b+c} + x = {avg * 4}$$\n\n따라서 $x={target}$"
    return q, exp, str(target)


# ==========================================
# [중2]
# ==========================================
def g2_sys(target):
    x = target
    y = random.randint(1, 5)
    a = x + y
    b = x - y

    q = f"다음 연립방정식의 해 $x$의 값을 구하시오.\n\n$$\\begin{{cases}}x + y = {a}\\\\x - y = {b}\\end{{cases}}$$"
    exp = f"두 식을 변끼리 더하면 $2x = {a + b}$ 이므로 $x = {target}$ 입니다."
    return q, exp, str(target)


def g2_func(target):
    slope = random.choice([1, 2, 3])
    x1 = random.randint(1, 4)
    y1 = slope * x1 + target

    q = f"기울기가 ${slope}$이고 점 $({x1}, {y1})$을 지나는 일차함수의 $y$절편을 구하시오."
    exp = f"일차함수 식을 $y = {slope}x + b$로 두고 점 $({x1}, {y1})$을 대입하면:\n\n$${y1} = {slope} \\times {x1} + b$$\n\n따라서 $y$절편 $b = {target}$ 입니다."
    return q, exp, str(target)


def g2_exp(target):
    c = random.randint(1, 5)
    total = target + c
    a = random.randint(1, total - 1)
    b = total - a

    q = f"다음 등식을 만족하는 $x$의 값을 구하시오.\n\n$$(2^{{{a}}} \\times 2^{{{b}}}) \\div 2^{{{c}}} = 2^x$$"
    exp = f"지수법칙에 의해 지수끼리 계산하면:\n\n$$x = {a} + {b} - {c} = {target}$$"
    return q, exp, str(target)


def g2_ineq(target):
    upper_bound = target + 1
    a = random.randint(1, 5)
    b = 2 * upper_bound - a

    q = f"다음 부등식을 만족하는 가장 큰 정수 $x$의 값을 구하시오.\n\n$$2x - {a} < {b}$$"
    exp = f"상수항을 이항하면:\n\n$$2x < {b + a}$$\n\n$$x < {upper_bound}$$\n\n따라서 이 범위를 만족하는 가장 큰 정수는 ${target}$입니다."
    return q, exp, str(target)


# ==========================================
# [중3]
# ==========================================
def g3_trigo(target):
    a = target - 1
    q = f"다음 식의 값을 구하시오.\n\n$$\\tan 45^\\circ + {a}$$"
    exp = f"$\\tan 45^\\circ = 1$ 이므로:\n\n$$1 + {a} = {target}$$"
    return q, exp, str(target)


def g3_quad_func(target):
    p = random.randint(1, 4)
    q_val = target - (p**2)
    q_str = (
        f"+ {q_val}" if q_val > 0 else (f"- {abs(q_val)}" if q_val < 0 else "")
    )

    q = f"이차함수 $y = (x - {p})^2 {q_str}$ 의 그래프가 $y$축과 만나는 점의 $y$좌표를 구하시오."
    exp = f"$y$축과 만나는 점의 $y$좌표는 $x = 0$일 때의 $y$값이므로:\n\n$$y = (0 - {p})^2 {q_str} = {p**2} {q_str} = {target}$$"
    return q, exp, str(target)


def g3_sqrt(target):
    a = random.randint(2, 6)
    offset = target - a
    off_str = (
        f"+ {offset}"
        if offset > 0
        else (f"- {abs(offset)}" if offset < 0 else "")
    )

    q = f"다음 식의 값을 구하시오.\n\n$$\\sqrt{{{a**2}}} {off_str}$$"
    exp = f"$$\\sqrt{{{a**2}}} = {a}$$ 이므로:\n\n$${a} {off_str} = {target}$$"
    return q, exp, str(target)


def g3_advanced(target):
    b = random.randint(1, 5)
    a = target + b

    q = f"다음 식을 계산하시오.\n\n$$(\\sqrt{{{a}}} + \\sqrt{{{b}}})(\\sqrt{{{a}}} - \\sqrt{{{b}}})$$"
    exp = f"합차 공식을 이용하면:\n\n$$(\\sqrt{{{a}}})^2 - (\\sqrt{{{b}}})^2 = {a} - {b} = {target}$$"
    return q, exp, str(target)


# ==========================================
# 난이도별 문제 (하 = 위의 기본 문제 그대로 / 중 = 약 1.5배 / 상 = 약 2배)
#  - 어느 난이도든 정답은 15, 9, 2 로 같다. (타임머신 암호 '1592' 유지) 문제 유형은 아래 '교과서 단원별 문제'에서 고른다.
#  - '몇 배 어렵다'는 문제 식에 들어가는 수·문자·연산자(+ - × ÷ ^ √ = < : 등)의 개수로 어림해서 맞췄다.
#    중·상은 항을 더하고 괄호를 늘리거나, 풀이 단계를 한두 번 더 거치게 만든 문제다.
#  - 각 함수 이름 끝의 _mid 는 중(약 1.5배), _high 는 상(약 2배) 이다. (이름에 아무것도 없으면 하)
# ==========================================
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


def _sqrt(n):
    return "\\sqrt{" + str(n) + "}"


SIN30 = "\\sin 30^\\circ"
COS60 = "\\cos 60^\\circ"


# ---------- [중1] 중 ----------
def g1_linear_mid(target):
    a = random.choice([2, 3, 4])
    b = random.randint(1, 5)
    c = random.choice([2, 3])
    f = random.randint(1, 4)
    h = random.randint(1, 6)
    d = random.choice([1, -1, -2])
    e = a * (target - b) + c * (target + f) + h - d * target

    q = (
        "다음 방정식의 해 $x$를 구하시오.\n\n"
        f"$${a}(x - {b}) + {c}(x + {f}) + {h} = {_coef(d)} {_sgn(e)}$$"
    )
    exp = (
        "괄호를 풀고 식을 정리하면:\n\n"
        f"$${a}x - {a*b} + {c}x + {c*f} + {h} = {_coef(d)} {_sgn(e)}$$\n\n"
        f"$${a + c - d}x = {e + a*b - c*f - h}$$\n\n따라서 $x={target}$"
    )
    return q, exp, str(target)


def g1_expr_mid(target):
    x_val = random.choice([-3, -2, 2, 3])
    a = random.choice([2, 3])
    b = random.randint(1, 4)
    c = random.choice([2, 4])
    m = random.choice([2, 3])
    f = random.randint(1, 4)
    n = random.choice([2, 3])
    k = target - (a * (x_val + b) - c * (x_val - 1) + m * (x_val + f) - n * x_val)

    q = (
        f"$x={x_val}$ 일 때, 다음 식의 값을 구하시오.\n\n"
        f"$${a}(x + {b}) - {c}(x - 1) + {m}(x + {f}) - {n}x {_sgn(k)}$$"
    )
    exp = (
        f"식의 $x$ 자리에 ${x_val}$를 대입하면:\n\n"
        f"$${a}\\times({x_val} + {b}) - {c}\\times({x_val} - 1) + {m}\\times({x_val} + {f})"
        f" - {n}\\times({x_val}) {_sgn(k)}$$\n\n"
        f"$$= {a}\\times({x_val + b}) - {c}\\times({x_val - 1}) + {m}\\times({x_val + f})"
        f" {_sgn(-n * x_val)} {_sgn(k)} = {target}$$"
    )
    return q, exp, str(target)


def _prop_candidates(target):
    """(px + a) : (qx - b) = c : d 를 만족하는 (p, q, a, b, c, d) 후보 목록."""
    found = []
    for p in (2, 3):
        for q_ in (2, 3):
            for b in range(1, 9):
                for c in range(2, 10):
                    for d in (2, 3, 4, 5):
                        bottom = q_ * target - b
                        if bottom <= 0 or (c * bottom) % d != 0 or d * p == c * q_:
                            continue
                        a = c * bottom // d - p * target
                        if a != 0 and -8 <= a <= 12:
                            found.append((p, q_, a, b, c, d))
    return found


def g1_prop_mid(target):
    p, q_, a, b, c, d = random.choice(_prop_candidates(target))

    q = (
        "다음 비례식을 만족하는 $x$의 값을 구하시오.\n\n"
        f"$$({p}x {_sgn(a)}) : ({q_}x - {b}) = {c} : {d}$$"
    )
    exp = (
        "내항의 곱과 외항의 곱은 같으므로:\n\n"
        f"$${d}({p}x {_sgn(a)}) = {c}({q_}x - {b})$$\n\n"
        f"$${d * p}x {_sgn(d * a)} = {c * q_}x - {c * b}$$\n\n"
        f"$${_coef(d * p - c * q_)} = {-c * b - d * a}$$\n\n따라서 $x={target}$"
    )
    return q, exp, str(target)


STAT_COUNT_WORDS = {7: "일곱", 10: "열"}


def _stat_problem(target, known_count, max_value):
    """known_count 개의 수와 x 의 평균이 정수가 되도록 수를 만든다."""
    n = known_count + 1
    nums = [random.randint(1, max_value) for _ in range(known_count)]
    total = sum(nums) + target
    while total % n != 0:
        nums[-1] += 1
        total += 1
    avg = total // n
    terms = " + ".join(str(v) for v in nums)

    q = (
        f"{STAT_COUNT_WORDS[n]} 개의 수 ${', '.join(str(v) for v in nums)}, x$ 의 평균이 ${avg}$일 때, "
        "$x$의 값을 구하시오."
    )
    exp = (
        "평균을 구하는 식을 세우면:\n\n"
        f"$$\\frac{{{terms} + x}}{{{n}}} = {avg}$$\n\n"
        f"$${sum(nums)} + x = {avg * n}$$\n\n따라서 $x={target}$"
    )
    return q, exp, str(target)


def g1_stat_mid(target):
    return _stat_problem(target, known_count=6, max_value=25)


# ---------- [중1] 상 ----------
def g1_linear_high(target):
    while True:
        a = random.choice([4, 5])
        c = random.choice([3, 4])
        h = random.choice([2, 3])
        d = random.choice([2, 3])
        if a + c - h - d >= 2:  # 정리한 x 의 계수가 1 이하가 되어 쉬워지지 않게
            break
    while True:
        b = random.randint(1, 5)
        f = random.randint(1, 4)
        k = random.randint(1, 4)
        g = random.randint(1, 4)
        e = a * (target - b) + c * (target + f) - h * (target - k) - d * (target - g)
        expr = f"{a}(x - {b}) + {c}(x + {f}) - {h}(x - {k}) = {d}(x - {g}) {_sgn(e)}".strip()
        if _vis_len(expr) <= EXPR_MAX_LEN:   # 식이 너무 길어지지 않게
            break

    q = f"다음 방정식의 해 $x$를 구하시오.\n\n$${expr}$$"
    exp = (
        "괄호를 풀고 식을 정리하면:\n\n"
        f"$${a}x - {a*b} + {c}x + {c*f} - {h}x + {h*k} = {d}x - {d*g} {_sgn(e)}$$\n\n"
        f"$${a + c - h - d}x = {e - d*g + a*b - c*f - h*k}$$\n\n따라서 $x={target}$"
    )
    return q, exp, str(target)


def g1_expr_high(target):
    # 식이 길어지지 않게 묶음 4개만 쓰고(따로 더하는 상수항 없음), 대신 분수 x 를 대입하게 해서 어렵게 한다.
    # 곱하는 수가 모두 짝수라 식의 값은 정수이고, 마지막 묶음의 상수 g 로 값을 target 에 맞춘다.
    while True:
        x_val = random.choice([Fraction(-1, 2), Fraction(1, 2), Fraction(-3, 2), Fraction(3, 2)])
        a, c, m, n = (random.choice([2, 4]) for _ in range(4))
        b, f = random.randint(1, 4), random.randint(1, 4)
        head = a * (x_val + b) - c * (x_val - 1) + m * (x_val + f)
        gs = [g for g in range(-6, 7) if g != 0 and head - n * (x_val - g) == target]
        if gs:
            g = random.choice(gs)
            break

    expr = f"{a}(x + {b}) - {c}(x - 1) + {m}(x + {f}) - {n}(x {_sgn(-g)})"
    xs = _frac(x_val)
    q = f"$x={xs}$ 일 때, 다음 식의 값을 구하시오.\n\n$${expr}$$"
    exp = (
        f"식의 $x$ 자리에 ${xs}$를 대입하면:\n\n"
        f"$${a}\\times({xs} + {b}) - {c}\\times({xs} - 1) + {m}\\times({xs} + {f})"
        f" - {n}\\times({xs} {_sgn(-g)})$$\n\n"
        f"$$= {a}\\times({_frac(x_val + b)}) - {c}\\times({_frac(x_val - 1)}) + {m}\\times({_frac(x_val + f)})"
        f" - {n}\\times({_frac(x_val - g)}) = {target}$$"
    )
    return q, exp, str(target)


def g1_prop_high(target):
    p, q_, a, b, c, d = random.choice(_prop_candidates(target))
    c1 = random.randint(1, c - 1)  # 뒤쪽 비의 항을 합·차로 쪼개서 한 번 더 계산하게 한다.
    d2 = random.randint(1, 4)

    q = (
        "다음 비례식을 만족하는 $x$의 값을 구하시오.\n\n"
        f"$$({p}x {_sgn(a)}) : ({q_}x - {b}) = ({c1} + {c - c1}) : ({d + d2} - {d2})$$"
    )
    exp = (
        f"먼저 뒤쪽 비를 계산하면 $({c1} + {c - c1}) : ({d + d2} - {d2}) = {c} : {d}$ 입니다.\n\n"
        "내항의 곱과 외항의 곱은 같으므로:\n\n"
        f"$${d}({p}x {_sgn(a)}) = {c}({q_}x - {b})$$\n\n"
        f"$${d * p}x {_sgn(d * a)} = {c * q_}x - {c * b}$$\n\n"
        f"$${_coef(d * p - c * q_)} = {-c * b - d * a}$$\n\n따라서 $x={target}$"
    )
    return q, exp, str(target)


def g1_stat_high(target):
    return _stat_problem(target, known_count=9, max_value=40)


# ---------- [중2] 중 ----------
def g2_sys_mid(target):
    p = random.choice([2, 3])
    q_ = random.choice([2, 3])
    r = random.choice([2, 3, 4])
    s = random.choice([2, 3])
    u = random.randint(1, 3)
    y0 = random.randint(1, 5)
    a = p * target + q_ * y0
    b = r * target - s * (y0 - u)

    q = (
        "다음 연립방정식의 해 $x$의 값을 구하시오.\n\n"
        f"$$\\begin{{cases}}{p}x + {q_}y = {a}\\\\{r}x - {s}(y - {u}) = {b}\\end{{cases}}$$"
    )
    exp = (
        "둘째 식의 괄호를 풀어 정리하면:\n\n"
        f"$${r}x - {s}y = {b - s * u}$$\n\n"
        f"첫째 식에 ${s}$를, 둘째 식에 ${q_}$를 곱한 뒤 변끼리 더하면:\n\n"
        f"$${p * s}x + {q_ * s}y = {a * s}$$\n\n"
        f"$${r * q_}x - {s * q_}y = {(b - s * u) * q_}$$\n\n"
        f"$${p * s + r * q_}x = {a * s + (b - s * u) * q_}$$\n\n따라서 $x={target}$"
    )
    return q, exp, str(target)


def g2_func_mid(target):
    m = random.choice([-2, -1, 1, 2, 3])
    x1 = random.randint(1, 3)
    x2 = x1 + random.randint(1, 3)
    y1, y2 = m * x1 + target, m * x2 + target

    q = f"두 점 $({x1}, {y1})$, $({x2}, {y2})$를 지나는 일차함수의 $y$절편을 구하시오."
    exp = (
        f"두 점을 지나므로 기울기는 $\\frac{{{y2} - ({y1})}}{{{x2} - {x1}}} = {m}$ 입니다.\n\n"
        f"일차함수 식을 $y = {_coef(m)} + b$로 두고 점 $({x1}, {y1})$을 대입하면:\n\n"
        f"$${y1} = {m} \\times {x1} + b$$\n\n"
        f"따라서 $y$절편 $b = {target}$ 입니다."
    )
    return q, exp, str(target)


def _split_three(total):
    """total 을 양의 정수 세 개로 나눈다. (total >= 3)"""
    a = random.randint(1, total - 2)
    b = random.randint(1, total - a - 1)
    return a, b, total - a - b


def g2_exp_mid(target):
    while True:
        c = random.randint(1, 4)
        e = random.randint(1, 4)
        a, b, d = _split_three(target + c + e)
        if a + b - c >= 1:  # 중간 계산의 지수가 0 이하가 되지 않게
            break

    q = (
        "다음 등식을 만족하는 $x$의 값을 구하시오.\n\n"
        f"$$(2^{{{a}}} \\times 2^{{{b}}}) \\div 2^{{{c}}} \\times 2^{{{d}}} \\div 2^{{{e}}} = 2^x$$"
    )
    exp = (
        "곱셈과 나눗셈은 앞에서부터 차례로 지수끼리 계산하면:\n\n"
        f"$$x = {a} + {b} - {c} + {d} - {e} = {target}$$"
    )
    return q, exp, str(target)


def g2_ineq_mid(target):
    q_ = random.choice([2, 3])
    r = random.choice([2, 3])
    p = q_ + r
    a = random.randint(1, 5)
    b = r * (target + 1) - a

    q = (
        "다음 부등식을 만족하는 가장 큰 정수 $x$의 값을 구하시오.\n\n"
        f"$${p}x - {a} < {_coef(q_)} + {b}$$"
    )
    exp = (
        "$x$항은 왼쪽으로, 상수항은 오른쪽으로 옮기면:\n\n"
        f"$${r}x < {a + b}$$\n\n"
        f"$$x < {target + 1}$$\n\n"
        f"따라서 이 범위를 만족하는 가장 큰 정수는 ${target}$입니다."
    )
    return q, exp, str(target)


# ---------- [중2] 상 ----------
def g2_sys_high(target):
    p = random.choice([2, 3])
    q_ = random.choice([2, 3])
    r = random.choice([2, 3])
    s = random.choice([2, 3])
    u = random.randint(1, 3)
    w = random.randint(1, 5)
    z = random.randint(1, 5)
    y0 = random.randint(1, 4)
    a = p * (target + q_ * y0) + w
    b = r * target - s * (y0 - u) + z

    q = (
        "다음 연립방정식의 해 $x$의 값을 구하시오.\n\n"
        f"$$\\begin{{cases}}{p}(x + {q_}y) + {w} = {a}\\\\{r}x - {s}(y - {u}) + {z} = {b}\\end{{cases}}$$"
    )
    e1 = a - w  # 첫째 식을 정리한 우변
    e2 = b - z - s * u  # 둘째 식을 정리한 우변
    exp = (
        "괄호를 풀고 상수항을 옮기면:\n\n"
        f"$${p}x + {p * q_}y = {e1}$$\n\n"
        f"$${r}x - {s}y = {e2}$$\n\n"
        f"첫째 식에 ${s}$를, 둘째 식에 ${p * q_}$를 곱한 뒤 변끼리 더하면:\n\n"
        f"$${p * s + r * p * q_}x = {e1 * s + e2 * p * q_}$$\n\n따라서 $x={target}$"
    )
    return q, exp, str(target)


def g2_func_high(target):
    m = random.choice([-2, -1, 1, 2, 3])
    k0 = random.choice([-4, -3, -2, 1, 3, 4])  # 평행한 첫 직선의 y절편 (정답과 다르게)
    x1, x2, x3 = random.sample(range(1, 7), 3)
    y1, y2 = m * x1 + k0, m * x2 + k0
    y3 = m * x3 + target

    q = (
        f"두 점 $({x1}, {y1})$, $({x2}, {y2})$를 지나는 직선과 평행하고 "
        f"점 $({x3}, {y3})$을 지나는 일차함수의 $y$절편을 구하시오."
    )
    exp = (
        f"두 점을 지나는 직선의 기울기는 $\\frac{{{y2} - ({y1})}}{{{x2} - {x1}}} = {m}$ 이고, "
        f"평행한 직선은 기울기가 같습니다.\n\n"
        f"일차함수 식을 $y = {_coef(m)} + b$로 두고 점 $({x3}, {y3})$을 대입하면:\n\n"
        f"$${y3} = {m} \\times {x3} + b$$\n\n"
        f"따라서 $y$절편 $b = {target}$ 입니다."
    )
    return q, exp, str(target)


def g2_exp_high(target):
    while True:
        a = random.choice([1, 2, 3])
        k = random.choice([2, 3])
        d = random.choice([1, 2])
        h = random.choice([2, 3])
        c = random.randint(1, 4)
        e = random.randint(1, 4)
        f = random.randint(1, 3)
        b = target - a * k + c - d * h + e - f
        if b < 1:
            continue
        s1 = a * k + b - c  # 앞에서부터 계산했을 때 중간 지수들이 0 이하가 되지 않게
        s2 = s1 + d * h - e
        if s1 >= 1 and s2 >= 1:
            break

    q = (
        "다음 등식을 만족하는 $x$의 값을 구하시오.\n\n"
        f"$$(2^{{{a}}})^{{{k}}} \\times 2^{{{b}}} \\div 2^{{{c}}} \\times (2^{{{d}}})^{{{h}}}"
        f" \\div 2^{{{e}}} \\times 2^{{{f}}} = 2^x$$"
    )
    exp = (
        f"$(2^{{{a}}})^{{{k}}} = 2^{{{a * k}}}$, $(2^{{{d}}})^{{{h}}} = 2^{{{d * h}}}$ 이므로, "
        "곱셈과 나눗셈은 앞에서부터 차례로 지수끼리 계산하면:\n\n"
        f"$$x = {a * k} + {b} - {c} + {d * h} - {e} + {f} = {target}$$"
    )
    return q, exp, str(target)


def g2_ineq_high(target):
    q_ = random.choice([1, 2])
    r = random.choice([2, 3])
    p = q_ + r
    a, c, d = (random.randint(1, 3) for _ in range(3))
    b = r * (target + 1) - p * a - c - q_ * d
    qp = str(q_) if q_ > 1 else ""

    q = (
        "다음 부등식을 만족하는 가장 큰 정수 $x$의 값을 구하시오.\n\n"
        f"$${p}(x - {a}) - {c} < {qp}(x + {d}) {_sgn(b)}$$"
    )
    exp = (
        "괄호를 풀면:\n\n"
        f"$${p}x - {p * a} - {c} < {qp}x + {q_ * d} {_sgn(b)}$$\n\n"
        "$x$항은 왼쪽으로, 상수항은 오른쪽으로 옮기면:\n\n"
        f"$${r}x < {r * (target + 1)}$$\n\n"
        f"$$x < {target + 1}$$\n\n"
        f"따라서 이 범위를 만족하는 가장 큰 정수는 ${target}$입니다."
    )
    return q, exp, str(target)


# ---------- [중3] 중 ----------
def g3_trigo_mid(target):
    a = random.choice([2, 4, 6, 8])
    b = target - a // 2

    q = f"다음 식의 값을 구하시오.\n\n$${SIN30} \\times {a} {_sgn(b)}$$"
    exp = (
        "$\\sin 30^\\circ = \\frac{1}{2}$ 이므로:\n\n"
        f"$$\\frac{{1}}{{2}} \\times {a} {_sgn(b)} = {a // 2} {_sgn(b)} = {target}$$"
    )
    return q, exp, str(target)


def g3_quad_func_mid(target):
    a = random.choice([2, 3])
    p = random.choice([1, 2])
    m = random.choice([1, 2])
    n = random.choice([-3, -2, -1, 1, 2, 3])
    q_val = target - n - a * (p + m) ** 2

    q = (
        f"이차함수 $y = {a}(x - {p})^2 {_sgn(q_val)}$ 의 그래프를 "
        f"$x$축 방향으로 ${m}$만큼, $y$축 방향으로 ${n}$만큼 평행이동한 그래프가 "
        "$y$축과 만나는 점의 $y$좌표를 구하시오."
    )
    exp = (
        f"평행이동한 그래프의 식은 $y = {a}(x - {p + m})^2 {_sgn(q_val + n)}$ 입니다.\n\n"
        f"$y$축과 만나는 점은 $x = 0$일 때이므로:\n\n"
        f"$$y = {a} \\times {(p + m) ** 2} {_sgn(q_val + n)} = {target}$$"
    )
    return q, exp, str(target)


def g3_sqrt_mid(target):
    a = random.randint(2, 6)
    m = random.choice([2, 3])
    off = target - m * a

    q = f"다음 식의 값을 구하시오.\n\n$${_sqrt(a ** 2)} \\times {m} {_sgn(off)}$$"
    exp = (
        f"$${_sqrt(a ** 2)} = {a}$$ 이므로:\n\n"
        f"$${a} \\times {m} {_sgn(off)} = {m * a} {_sgn(off)} = {target}$$"
    )
    return q, exp, str(target)


def g3_advanced_mid(target):
    while True:
        b = random.randint(1, 5)
        c = random.randint(2, 5)
        a = target + b - c
        if a >= 2:
            break

    q = (
        "다음 식을 계산하시오.\n\n"
        f"$$({_sqrt(a)} + {_sqrt(b)})({_sqrt(a)} - {_sqrt(b)}) + {_sqrt(c)} \\times {_sqrt(c)}$$"
    )
    exp = (
        "합차 공식과 $\\sqrt{c} \\times \\sqrt{c} = c$ 를 이용하면:\n\n"
        f"$$({_sqrt(a)})^2 - ({_sqrt(b)})^2 + {c} = {a} - {b} + {c} = {target}$$"
    )
    return q, exp, str(target)


# ---------- [중3] 상 ----------
def g3_trigo_high(target):
    a = random.choice(range(2, 2 * target, 2))
    c = 2 * target - a

    q = f"다음 식의 값을 구하시오.\n\n$${SIN30} \\times {a} + {COS60} \\times {c}$$"
    exp = (
        "$\\sin 30^\\circ = \\frac{1}{2}$, $\\cos 60^\\circ = \\frac{1}{2}$ 이므로:\n\n"
        f"$$\\frac{{1}}{{2}} \\times {a} + \\frac{{1}}{{2}} \\times {c} = {a // 2} + {c // 2} = {target}$$"
    )
    return q, exp, str(target)


def g3_quad_func_high(target):
    a = random.choice([2, 3])
    p = random.choice([1, 2])
    m = 1
    h = 1
    n = random.choice([-3, -2, -1, 1, 2, 3])
    k = random.choice([-3, -2, -1, 1, 2, 3])
    # 평행이동(m, n) -> x축 대칭 -> 평행이동(h, k) 한 뒤 x = 0 에서의 값이 target 이 되도록 q_val 을 정한다.
    q_val = k - n - target - a * (p + m + h) ** 2

    q = (
        f"이차함수 $y = {a}(x - {p})^2 {_sgn(q_val)}$ 의 그래프를 "
        f"$x$축 방향으로 ${m}$만큼, $y$축 방향으로 ${n}$만큼 평행이동한 뒤 "
        "$x$축에 대하여 대칭이동하고, 다시 "
        f"$x$축 방향으로 ${h}$만큼, $y$축 방향으로 ${k}$만큼 평행이동한 그래프가 "
        "$y$축과 만나는 점의 $y$좌표를 구하시오."
    )
    exp = (
        f"처음 평행이동한 그래프의 식은 $y = {a}(x - {p + m})^2 {_sgn(q_val + n)}$ 이고, "
        f"$x$축에 대하여 대칭이동하면 $y$ 대신 $-y$를 넣어 "
        f"$y = -{a}(x - {p + m})^2 {_sgn(-(q_val + n))}$ 입니다.\n\n"
        f"다시 $x$축 방향으로 ${h}$만큼, $y$축 방향으로 ${k}$만큼 평행이동하면 "
        f"$y = -{a}(x - {p + m + h})^2 {_sgn(-(q_val + n) + k)}$ 입니다.\n\n"
        f"$y$축과 만나는 점은 $x = 0$일 때이므로:\n\n"
        f"$$y = -{a} \\times {(p + m + h) ** 2} {_sgn(-(q_val + n) + k)} = {target}$$"
    )
    return q, exp, str(target)


def g3_sqrt_high(target):
    a = random.randint(2, 5)
    m = random.choice([2, 3])
    b = random.randint(2, 6)
    off = target - m * a - b

    q = (
        "다음 식의 값을 구하시오.\n\n"
        f"$${_sqrt(a ** 2)} \\times {m} + {_sqrt(b ** 2)} {_sgn(off)}$$"
    )
    exp = (
        f"${_sqrt(a ** 2)} = {a}$, ${_sqrt(b ** 2)} = {b}$ 이므로:\n\n"
        f"$${a} \\times {m} + {b} {_sgn(off)} = {m * a + b} {_sgn(off)} = {target}$$"
    )
    return q, exp, str(target)


def g3_advanced_high(target):
    while True:
        b = random.randint(1, 5)
        d = random.randint(1, 5)
        c = random.randint(2, 9)
        a = target + b + d - c
        if a >= 2 and c != d:  # 두 번째 묶음이 0 이 되어 쉬워지지 않게
            break

    q = (
        "다음 식을 계산하시오.\n\n"
        f"$$({_sqrt(a)} + {_sqrt(b)})({_sqrt(a)} - {_sqrt(b)})"
        f" + ({_sqrt(c)} + {_sqrt(d)})({_sqrt(c)} - {_sqrt(d)})$$"
    )
    exp = (
        "합차 공식을 이용하면:\n\n"
        f"$$({a} - {b}) + ({c} - {d}) = {a - b} {_sgn(c - d)} = {target}$$"
    )
    return q, exp, str(target)


# ==========================================
# 교과서(중1~중3 수학) 단원별 문제
#  - 학년 버튼을 누르면 그 학년 교과서의 단원 중 '서로 다른 3개 단원'을 무작위로 뽑고,
#    단원마다 문제 유형(함수) 하나를 무작위로 뽑는다. 정답은 언제나 15, 9, 2 (타임머신 암호 '1592' 유지).
#  - 문제 함수는 모두 (target, level) 을 받는다. level: 1=하, 2=중(약 1.5배), 3=상(약 2배).
#    난이도는 문제 식에 들어가는 수·문자·연산자 개수로 어림했고, 조건(항·각·계급·도형 수 등)을 늘려서 올렸다.
#  - 'x 의 값은?' 처럼 답이 정해진 문제는 답(target)에서 거꾸로 수를 정해 만들고,
#    개수를 세는 문제는 조건을 무작위로 뽑아 직접 세어 본 값이 target 과 같을 때만 쓴다(_roll).
#  - 기존 문제(g1_linear 등 12가지)도 알맞은 단원의 문제 유형으로 그대로 쓴다.
# ==========================================
def _roll(make, solve, target, tries=60000):
    """make() 로 무작위 조건을 뽑고 solve(조건) 이 target 과 같을 때까지 다시 뽑는다."""
    for _ in range(tries):
        spec = make()
        if spec is not None and solve(spec) == target:
            return spec
    raise RuntimeError(f"조건을 만족하는 문제를 만들지 못했습니다 (target={target})")


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
            n += 0 if tex[i + 1:j] in ("frac", "sqrt", "left", "right", "overline") else 1
            i = j
            continue
        if ch not in " {}^":
            n += 1
        i += 1
    return n


def _pn(n):
    """부호를 붙여 괄호로 묶은 수. 7 -> '(+7)', -7 -> '(-7)'."""
    return f"({'+' if n > 0 else '-'}{abs(n)})"


def _paren(n):
    """음수만 괄호로 묶는다. -3 -> '(-3)', 3 -> '3'."""
    return f"({n})" if n < 0 else str(n)


def _lin(a, b, var="x"):
    """일차식 ax+b 를 문자열로. (3, -4) -> '3x - 4', (1, 0) -> 'x'."""
    return f"{_coef(a, var)} {_sgn(b)}".strip()


def _ang(a, b, var="x"):
    """각의 크기 (ax+b)° . a 가 0 이면 그냥 b° ."""
    return f"{b}^\\circ" if a == 0 else f"({_lin(a, b, var)})^\\circ"


def _frac(n, d=1):
    """기약분수 \\frac{n}{d}. 정수면 그냥 정수. 음수는 앞에 -."""
    f = Fraction(n, d)
    if f.denominator == 1:
        return str(f.numerator)
    sign = "-" if f < 0 else ""
    return f"{sign}\\frac{{{abs(f.numerator)}}}{{{f.denominator}}}"


def _sqrt_or_int(n):
    """n 이 제곱수면 정수, 아니면 \\sqrt{n}."""
    r = math.isqrt(n)
    return str(r) if r * r == n else f"\\sqrt{{{n}}}"


def _nonzero(lo, hi):
    return random.choice([n for n in range(lo, hi + 1) if n])


def _shift(points, lo=-9, hi=9):
    """도형 전체를 옮겨서 모든 좌표가 lo~hi 안에 들어오게 한다."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    dx = random.randint(lo - min(xs), hi - max(xs))
    dy = random.randint(lo - min(ys), hi - max(ys))
    return [(x + dx, y + dy) for x, y in points]


def _pt(name, p):
    return f"{name}({p[0]}, {p[1]})"


def _fact(n):
    """n 의 소인수분해 {소수: 지수}."""
    out, p = {}, 2
    while p * p <= n:
        while n % p == 0:
            out[p] = out.get(p, 0) + 1
            n //= p
        p += 1
    if n > 1:
        out[n] = out.get(n, 0) + 1
    return out


def _fact_tex(n):
    """소인수분해 결과를 LaTeX 로. 72 -> '2^{3} \\times 3^{2}'."""
    return " \\times ".join(
        f"{p}^{{{e}}}" if e > 1 else str(p) for p, e in sorted(_fact(n).items())
    )


def _levels(low, mid, high):
    """기존 난이도별 함수 3개를 (target, level) 형태 하나로 묶는다."""

    def pick(target, level):
        return (low, mid, high)[level - 1](target)

    pick.__name__ = low.__name__
    return pick


# ==========================================
# [중1] 교과서 단원별 문제
# ==========================================
# ---------- 1. 소인수분해 ----------
def f_gcd(target, level):
    """여러 수의 최대공약수 (수의 개수: 하 2, 중 3, 상 4)."""
    n = level + 1
    while True:
        mult = random.sample(range(2, 10), n)
        if math.gcd(*mult) == 1:
            break
    nums = [target * m for m in mult]
    word = {2: "두", 3: "세", 4: "네"}[n]
    common = _fact_tex(target)
    q = f"다음 {word} 수의 최대공약수를 구하시오.\n\n$${', '.join(map(str, nums))}$$"
    lines = "\n\n".join(f"$${v} = {_fact_tex(v)}$$" for v in nums)
    if common == str(target):
        tail = f"공통인 소인수는 ${target}$뿐이므로 최대공약수는 ${target}$입니다."
    else:
        tail = f"공통인 소인수의 거듭제곱에서 지수가 작은 것을 택하여 곱하면:\n\n$${common} = {target}$$"
    exp = f"각 수를 소인수분해하면:\n\n{lines}\n\n{tail}"
    return q, exp, str(target)


def f_divcount(target, level):
    """약수의 개수가 주어졌을 때 모르는 지수 x 구하기 (소인수의 개수: 하 2, 중 3, 상 4)."""
    primes = random.sample([2, 3, 5, 7], level + 1)
    exps = {p: random.choice([1, 2, 3]) for p in primes[1:]}
    k = 1
    for e in exps.values():
        k *= e + 1
    total = (target + 1) * k
    parts = [f"{primes[0]}^{{x}}"] + [
        f"{p}^{{{e}}}" if e > 1 else str(p) for p, e in exps.items()
    ]
    random.shuffle(parts)
    expr = " \\times ".join(parts)
    q = f"${expr}$의 약수의 개수가 ${total}$개일 때, 자연수 $x$의 값을 구하시오."
    factors = " \\times ".join(["(x+1)"] + [f"({e}+1)" for e in exps.values()])
    exp = (
        "약수의 개수는 각 소인수의 (지수 + 1)을 모두 곱한 것과 같으므로:\n\n"
        f"$${factors} = {total}$$\n\n$$x+1 = {target + 1}$$\n\n따라서 $x={target}$"
    )
    return q, exp, str(target)


# ---------- 2. 정수와 유리수 ----------
def f_intsum(target, level):
    """정수의 덧셈·뺄셈 (항의 개수: 하 2, 중 3, 상 4)."""
    k = level + 1
    while True:
        first = _nonzero(-12, 12)
        ops = [random.choice("+-") for _ in range(k - 1)]
        mids = [_nonzero(-12, 12) for _ in range(k - 2)]
        partial = first + sum(n if o == "+" else -n for o, n in zip(ops, mids))
        need = target - partial
        last = need if ops[-1] == "+" else -need
        if 1 <= abs(last) <= 20:
            break
    terms = [first] + mids + [last]
    expr = _pn(first) + "".join(f" {o} {_pn(n)}" for o, n in zip(ops, terms[1:]))
    signed = [first] + [n if o == "+" else -n for o, n in zip(ops, terms[1:])]
    pos = sum(s for s in signed if s > 0)
    neg = sum(s for s in signed if s < 0)
    gathered = " + ".join(
        [_pn(pos)] * (pos > 0) + [_pn(neg)] * (neg < 0)
    )
    q = f"다음을 계산하시오.\n\n$${expr}$$"
    exp = (
        "뺄셈은 빼는 수의 부호를 바꾸어 더하는 것으로 고치면:\n\n"
        f"$${' + '.join(_pn(s) for s in signed)}$$\n\n"
        f"양수끼리, 음수끼리 모아서 계산하면:\n\n$${gathered} = {target}$$"
    )
    return q, exp, str(target)


def _mixed_term():
    kind = random.choice(["mul", "div", "pow"])
    if kind == "mul":
        a, b = random.randint(2, 9), random.choice([-1, 1]) * random.randint(2, 9)
        return f"{a} \\times {_paren(b)}", a * b
    if kind == "div":
        d, q_ = random.randint(2, 6), random.choice([-1, 1]) * random.randint(2, 9)
        return f"{_paren(d * q_)} \\div {d}", q_
    base, e = random.choice([-4, -3, -2, 2, 3, 4]), random.choice([2, 3])
    return f"{_paren(base)}^{{{e}}}", base ** e


def f_mixed(target, level):
    """덧셈·뺄셈·곱셈·나눗셈·거듭제곱이 섞인 계산 (계산 덩어리 수: 하 2, 중 3, 상 4)."""
    n = level + 1
    while True:
        terms = [_mixed_term() for _ in range(n)]
        ops = ["+"] + [random.choice("+-") for _ in range(n - 1)]
        partial = sum(v if o == "+" else -v for o, (_, v) in zip(ops, terms))
        c = target - partial
        if c == 0 or abs(c) > 30:
            continue
        expr = terms[0][0] + "".join(
            f" {o} {tex}" for o, (tex, _) in zip(ops[1:], terms[1:])
        )
        expr += f" {_sgn(c)}"
        if _vis_len(expr) <= EXPR_MAX_LEN - 2:   # 식이 너무 길어지지 않게
            break
    lines = "\n\n".join(f"$${tex} = {v}$$" for tex, v in terms)
    vals = str(terms[0][1]) + "".join(
        f" {o} {_paren(v)}" for o, (_, v) in zip(ops[1:], terms[1:])
    )
    q = f"다음을 계산하시오.\n\n$${expr}$$"
    exp = (
        "거듭제곱과 곱셈, 나눗셈을 먼저 계산하면:\n\n"
        f"{lines}\n\n덧셈과 뺄셈을 계산하면:\n\n$${vals} {_sgn(c)} = {target}$$"
    )
    return q, exp, str(target)


# ---------- 3. 문자와 식 ----------
def f_linexpr(target, level):
    """일차식의 덧셈·뺄셈 결과에서 계수와 상수항의 합 (괄호 묶음 수: 하 1, 중 2, 상 4).

    상은 식이 너무 길어지지 않도록 따로 더하는 상수항 없이 마지막 묶음의 상수로 값을 맞춘다."""
    g = (1, 2, 4)[level - 1]
    while True:
        ms = [random.choice([2, 3, 4, 5])] + [
            random.choice([-5, -4, -3, -2, 2, 3, 4, 5]) for _ in range(g - 1)
        ]
        a = [random.choice([-3, -2, -1, 1, 1, 2, 3, 4]) for _ in range(g)]
        b = [_nonzero(-9, 9) for _ in range(g)]
        p = sum(m * x for m, x in zip(ms, a))
        if level == 3:
            k = 0
            rest = target - p - sum(m * y for m, y in zip(ms[:-1], b[:-1]))
            b[-1], rem = divmod(rest, ms[-1])
            if rem or not 1 <= abs(b[-1]) <= 9:
                continue
            s = sum(m * y for m, y in zip(ms, b))
        else:
            s = sum(m * y for m, y in zip(ms, b))
            k = target - p - s
            if k == 0 or abs(k) > 30:
                continue
        if p == 0 or abs(p) > 30:
            continue
        expr = f"{ms[0]}({_lin(a[0], b[0])})" + "".join(
            f" {'+' if m > 0 else '-'} {abs(m)}({_lin(x, y)})"
            for m, x, y in zip(ms[1:], a[1:], b[1:])
        )
        expr = f"{expr} {_sgn(k)}".strip()
        if _vis_len(expr) <= EXPR_MAX_LEN - 2:
            break
    q = (
        f"식 ${expr}$을 계산하면 $x$의 계수는 $a$, 상수항은 $b$입니다. "
        "$a+b$의 값을 구하시오."
    )
    gathered = f"{_coef(p)} {_sgn(s)} {_sgn(k)}".strip()
    exp = (
        "분배법칙으로 괄호를 풀고 동류항끼리 모으면:\n\n"
        f"$${gathered}$$\n\n"
        f"$${_lin(p, s + k)}$$\n\n"
        f"이므로 $a={p}$, $b={s + k}$이고 $a+b={target}$"
    )
    return q, exp, str(target)


# ---------- 4. 좌표평면과 그래프 ----------
def f_prop(target, level):
    """정비례·반비례 (하: 정비례 그래프 위의 점, 중: 정비례 + 반비례 상수의 곱, 상: 정비례 + 반비례 상수의 합)."""
    if level == 1:
        while True:
            p, c = random.randint(1, 6), random.randint(2, 12)
            # 지나는 점의 x 좌표(p)를 그대로 묻지 않도록 c != p 로 한다.
            if (target * p) % c == 0 and target * p // c != p and c != p:
                q_ = target * p // c
                break
        q = (
            f"정비례 관계 $y=ax$의 그래프가 지나는 한 점이 $({p}, {q_})$이다. "
            f"$x={c}$일 때 $y$의 값을 구하시오."
        )
        exp = (
            f"$y=ax$에 $x={p}$, $y={q_}$의 값을 대입하면 $a={_frac(q_, p)}$이므로 $y={_frac(q_, p)}x$입니다.\n\n"
            f"$x={c}$를 대입하면 $y={target}$"
        )
        return q, exp, str(target)
    if level == 3:
        a = random.randint(1, target - 1)
        b = target - a
    else:
        a, b = random.choice(
            [(a, target // a) for a in range(1, 16) if target % a == 0 and target // a <= 15]
        )
    p = random.randint(1, 5)
    r = random.choice([d for d in range(1, b + 1) if b % d == 0])
    ask = "a+b" if level == 3 else "ab"
    sym = "+" if level == 3 else "\\times"
    q = (
        f"정비례 관계 $y=ax$의 그래프가 지나는 한 점이 $({p}, {a * p})$이고, "
        f"반비례 관계 $y=\\frac{{b}}{{x}}$의 그래프가 지나는 한 점이 $({r}, {b // r})$일 때, "
        f"상수 $a$, $b$에 대하여 ${ask}$의 값을 구하시오."
    )
    exp = (
        f"$y=ax$에 $x={p}$, $y={a * p}$의 값을 대입하면 $a={a}$입니다.\n\n"
        f"$y=\\frac{{b}}{{x}}$에 $x={r}$, $y={b // r}$의 값을 대입하면 $b={r}\\times{b // r}={b}$입니다.\n\n"
        f"따라서 ${ask}={a}{sym}{b}={target}$"
    )
    return q, exp, str(target)


def _factor_pairs(n, lo=1, hi=12):
    return [(a, n // a) for a in range(lo, hi + 1) if n % a == 0 and lo <= n // a <= hi]


def f_area(target, level):
    """좌표평면 위 도형의 넓이 (하: 삼각형, 중: 직사각형, 상: 사다리꼴)."""
    if level == 1:
        bw, h = random.choice(_factor_pairs(2 * target))
        pts = [(0, 0), (bw, 0), (random.randint(0, bw), h * random.choice([1, -1]))]
        name, shape = "ABC", "삼각형"
        exp_core = (
            f"선분 $AB$의 길이는 ${bw}$이고, 점 $C$에서 직선 $AB$까지의 거리는 ${h}$이므로\n\n"
            f"$$\\frac{{1}}{{2}}\\times{bw}\\times{h}={target}$$"
        )
    elif level == 2:
        w, h = random.choice(_factor_pairs(target))
        pts = [(0, 0), (w, 0), (w, h), (0, h)]
        name, shape = "ABCD", "직사각형"
        exp_core = (
            f"가로의 길이는 ${w}$, 세로의 길이는 ${h}$이므로\n\n$${w}\\times{h}={target}$$"
        )
    else:
        while True:
            h = random.choice([d for d in range(1, 13) if (2 * target) % d == 0])
            s = 2 * target // h
            if s < 3 or s > 20:
                continue
            a = random.randint(1, s - 1)
            c = s - a
            u = random.randint(-2, 3)
            if a == c or a > 12 or c > 12:
                continue
            pts = [(0, 0), (a, 0), (u + c, h), (u, h)]
            if max(p[0] for p in pts) - min(p[0] for p in pts) <= 18:
                break
        name, shape = "ABCD", "사다리꼴"
        exp_core = (
            f"변 $AB$와 변 $DC$가 평행하고 길이가 각각 ${a}$, ${c}$, 높이가 ${h}$이므로\n\n"
            f"$$\\frac{{1}}{{2}}\\times({a}+{c})\\times{h}={target}$$"
        )
    if random.random() < 0.5:
        pts = [(y, x) for x, y in pts]
    pts = _shift(pts)
    listing = ", ".join(f"${_pt(n, p)}$" for n, p in zip(name, pts))
    q = (
        f"꼭짓점의 좌표가 {listing}인 "
        f"{shape} ${name}$의 넓이를 구하시오."
        + ("\n\n(단, $\\overline{AB}\\parallel\\overline{DC}$이다.)" if level == 3 else "")
    )
    exp = f"좌표를 이용하여 길이를 구하면\n\n{exp_core}"
    return q, exp, str(target)


# ---------- 5. 기본 도형과 작도 ----------
def _split(total, n, lo, hi):
    """합이 total 인 n 개의 정수 (각각 lo~hi)."""
    while True:
        parts = [random.randint(lo, hi) for _ in range(n - 1)]
        last = total - sum(parts)
        if lo <= last <= hi:
            return parts + [last]


def _angle_exprs(values, t):
    """각 값 v 를 (a x + b)° 꼴로 바꾼다(x=t). 문자가 들어간 각이 2개 이상."""
    while True:
        a = [random.choice([0, 1, 1, 2, 2, 3]) for _ in values]
        if sum(1 for x in a if x) >= 2:
            return [(ai, v - ai * t) for ai, v in zip(a, values)]


def _sum_ab(exprs):
    return sum(a for a, _ in exprs), sum(b for _, b in exprs)


def f_line_angle(target, level):
    """한 직선 위의 평각을 여러 각으로 나눈 문제 (각의 개수: 하 3, 중 4, 상 6)."""
    n = (3, 4, 6)[level - 1]
    lo, hi = {3: (20, 120), 4: (15, 100), 6: (10, 70)}[n]
    exprs = _angle_exprs(_split(180, n, lo, hi), target)
    A, B = _sum_ab(exprs)
    word = {3: "세", 4: "네", 6: "여섯"}[n]
    listing = ", ".join(f"${_ang(a, b)}$" for a, b in exprs)
    q = (
        f"한 직선 위의 점 $O$에서 같은 쪽으로 반직선을 그어 평각을 {word} 각으로 나누었다. "
        f"{word} 각의 크기가 차례로 {listing}일 때, $x$의 값을 구하시오."
    )
    exp = (
        "평각의 크기는 $180^\\circ$이므로\n\n"
        f"$${' + '.join(_ang(a, b) for a, b in exprs)} = 180^\\circ$$\n\n"
        f"$${_lin(A, B)} = 180$$\n\n따라서 $x={target}$"
    )
    return q, exp, str(target)


def _pair_equal(t):
    """엇각(동위각)처럼 크기가 같은 두 각 식 (x=t 일 때 같은 값)."""
    a, c = random.sample(range(1, 6), 2)
    v = random.randint(30, 150)
    return (a, v - a * t), (c, v - c * t)


def _pair_supp(t):
    """같은 쪽 두 내각(동측내각)처럼 합이 180° 인 두 각 식."""
    a, c = random.sample(range(1, 6), 2)
    v = random.randint(40, 140)
    return (a, v - a * t), (c, 180 - v - c * t)


def f_parallel_angle(target, level):
    """평행선의 각 (하: 엇각·동위각이 같다 / 중: 꺾인 선 / 상: 엇각 x + 동측내각 y → x+y)."""
    if level == 1:
        kind = random.choice(["엇각", "동위각"])
        (a1, b1), (a2, b2) = _pair_equal(target)
        q = (
            f"평행한 두 직선 $l$, $m$이 다른 한 직선과 만날 때, {kind}의 크기가 각각 "
            f"${_ang(a1, b1)}$, ${_ang(a2, b2)}$이다. $x$의 값을 구하시오."
        )
        exp = (
            f"{kind}의 크기는 서로 같으므로\n\n$${_lin(a1, b1)} = {_lin(a2, b2)}$$\n\n"
            f"따라서 $x={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            v1, v2 = random.randint(20, 80), random.randint(20, 80)
            a1, a2, a3 = (random.randint(1, 5) for _ in range(3))
            if a3 != a1 + a2:
                break
        b1, b2, b3 = v1 - a1 * target, v2 - a2 * target, v1 + v2 - a3 * target
        q = (
            "평행한 두 직선 $l$, $m$ 사이의 점 $P$에서 선분이 꺾여 있다. "
            "점 $P$를 지나 $l$에 평행한 직선을 그었을 때 생기는 두 엇각의 크기가 각각 "
            f"${_ang(a1, b1)}$, ${_ang(a2, b2)}$이고, 꺾인 점에서의 각 $\\angle P$의 크기가 "
            f"${_ang(a3, b3)}$일 때, $x$의 값을 구하시오."
        )
        exp = (
            "$\\angle P$는 두 엇각의 크기의 합과 같으므로\n\n"
            f"$${_lin(a3, b3)} = ({_lin(a1, b1)}) + ({_lin(a2, b2)})$$\n\n"
            f"따라서 $x={target}$"
        )
        return q, exp, str(target)
    x = random.randint(1, target - 1)
    y = target - x
    (a1, b1), (a2, b2) = _pair_equal(x)
    (c1, d1), (c2, d2) = _pair_supp(y)
    q = (
        "평행한 두 직선 $l$, $m$이 다른 한 직선과 만날 때 엇각의 크기가 각각 "
        f"${_ang(a1, b1)}$, ${_ang(a2, b2)}$이고, 또 다른 한 직선과 만날 때 같은 쪽에 있는 두 내각(동측내각)의 "
        f"크기가 각각 ${_ang(c1, d1, 'y')}$, ${_ang(c2, d2, 'y')}$이다. $x+y$의 값을 구하시오."
    )
    exp = (
        f"엇각의 크기는 서로 같으므로 ${_lin(a1, b1)}={_lin(a2, b2)}$, 즉 $x={x}$\n\n"
        f"동측내각의 합은 $180^\\circ$이므로 ${_lin(c1 + c2, d1 + d2, 'y')}=180$, 즉 $y={y}$\n\n"
        f"따라서 $x+y={target}$"
    )
    return q, exp, str(target)


# ---------- 6. 평면도형의 성질 ----------
def f_polygon_angle(target, level):
    """다각형의 내각의 크기의 합 (사각형 → 육각형 → 칠각형). 각을 너무 많이 늘어놓지 않도록 칠각형까지만 쓴다."""
    n = (4, 6, 7)[level - 1]
    total = 180 * (n - 2)
    exprs = _angle_exprs(_split(total, n, 50, 170), target)
    A, B = _sum_ab(exprs)
    name = {4: "사각형", 6: "육각형", 7: "칠각형"}[n]
    listing = ", ".join(f"${_ang(a, b)}$" for a, b in exprs)
    q = f"{name}의 내각의 크기가 차례로 {listing}일 때, $x$의 값을 구하시오."
    exp = (
        f"{n}각형의 내각의 크기의 합은 $180^\\circ\\times({n}-2)={total}^\\circ$이므로\n\n"
        f"$${_lin(A, B)} = {total}$$\n\n따라서 $x={target}$"
    )
    return q, exp, str(target)


def f_sector(target, level):
    """부채꼴의 호의 길이·넓이로 원의 반지름 구하기 (부채꼴 수: 하 1, 중 2, 상 3)."""
    k = level
    mode = random.choice(["arc", "area"])

    def make():
        angs = [random.choice([20, 30, 40, 45, 60, 72, 90, 100, 120, 135, 150, 180]) for _ in range(k)]
        X = sum(angs)
        if X > 360:
            return None
        val = Fraction(target * X, 180) if mode == "arc" else Fraction(target * target * X, 360)
        return (angs, val) if val.denominator == 1 and val >= 1 else None

    angs, val = _roll(make, lambda s: target, target)
    X = sum(angs)
    pi = "\\pi" if val == 1 else f"{val}\\pi"
    what = "호의 길이" if mode == "arc" else "넓이"
    unit = "cm" if mode == "arc" else "cm²"
    if k == 1:
        q = (
            f"중심각의 크기가 ${angs[0]}^\\circ$인 부채꼴의 {what}가 ${pi}$ {unit}일 때, "
            "이 부채꼴의 반지름의 길이를 구하시오."
        )
    else:
        listing = ", ".join(f"${a}^\\circ$" for a in angs)
        q = (
            f"같은 원에서 중심각의 크기가 각각 {listing}인 {k}개의 부채꼴의 {what}의 합이 "
            f"${pi}$ {unit}일 때, 이 원의 반지름의 길이를 구하시오."
        )
    if mode == "arc":
        formula = f"$$2\\pi r\\times\\frac{{{X}}}{{360}}={pi}$$"
        head = "중심각의 크기의 합이 " + f"${X}^\\circ$이므로 반지름을 $r$ cm라 하면 호의 길이의 합은"
    else:
        formula = f"$$\\pi r^2\\times\\frac{{{X}}}{{360}}={pi}$$"
        head = "중심각의 크기의 합이 " + f"${X}^\\circ$이므로 반지름을 $r$ cm라 하면 넓이의 합은"
    exp = f"{head}\n\n{formula}\n\n따라서 $r={target}$"
    return q, exp, str(target)


# ---------- 7. 입체도형의 성질 ----------
def f_cuboid_cut(target, level):
    """직육면체의 부피로 높이 구하기 (하: 그대로, 중: 정육면체 1개를 잘라 냄, 상: 정육면체와 직육면체를 잘라 냄)."""
    while True:
        a, b = random.randint(3, 12), random.randint(3, 12)
        cuts = []  # 잘라 낸 입체의 (가로, 세로, 높이)
        if level == 2:
            c = random.randint(1, 3)
            cuts = [(c, c, c)]
        elif level == 3:
            c1, c2, d2 = random.randint(1, 2), random.randint(1, 2), random.randint(1, 3)
            cuts = [(c1, c1, c1), (c2, c2, d2)]
        if all(h <= target for _, _, h in cuts) and sum(w for w, _, _ in cuts) <= min(a, b):
            break
    cut_vol = sum(w * l * h for w, l, h in cuts)
    vol = a * b * target - cut_vol
    if level == 1:
        q = (
            f"가로의 길이가 ${a}$ cm, 세로의 길이가 ${b}$ cm인 직육면체의 부피가 "
            f"${vol}$ cm³일 때, 이 직육면체의 높이를 구하시오."
        )
        exp = f"높이를 $h$ cm라 하면 ${a}\\times{b}\\times h={vol}$이므로 $h={target}$"
        return q, exp, str(target)
    if level == 2:
        c = cuts[0][0]
        cut_text = f"한 모서리의 길이가 ${c}$ cm인 정육면체 모양을 잘라 낸"
        cut_expr = f"{c}^3"
    else:
        (c1, _, _), (c2, _, d2) = cuts
        cut_text = (
            f"한 모서리의 길이가 ${c1}$ cm인 정육면체 모양과 밑면이 한 변의 길이가 ${c2}$ cm인 "
            f"정사각형이고 높이가 ${d2}$ cm인 직육면체 모양을 서로 겹치지 않게 잘라 낸"
        )
        cut_expr = f"{c1}^3 + {c2}^2\\times{d2}"
    q = (
        f"가로의 길이가 ${a}$ cm, 세로의 길이가 ${b}$ cm인 직육면체에서 {cut_text} "
        f"입체도형의 부피가 ${vol}$ cm³일 때, 처음 직육면체의 높이를 구하시오."
    )
    exp = (
        "처음 직육면체의 높이를 $h$ cm라 하면 (처음 부피) - (잘라 낸 부피) = (남은 부피)이므로\n\n"
        f"$${a}\\times{b}\\times h - ({cut_expr}) = {vol}$$\n\n"
        f"$${a * b}h = {vol + cut_vol}$$\n\n따라서 $h={target}$"
    )
    return q, exp, str(target)


def f_pour(target, level):
    """부피가 같게 그릇에 옮겨 담을 때 높이 구하기 (하: 원기둥, 중: 원뿔 → 원기둥, 상: 원뿔 두 개 → 원기둥)."""
    if level == 1:
        r = random.randint(2, 9)
        q = (
            f"밑면의 반지름의 길이가 ${r}$ cm인 원기둥의 부피가 ${r * r * target}\\pi$ cm³일 때, "
            "이 원기둥의 높이를 구하시오."
        )
        exp = f"높이를 $h$ cm라 하면 $\\pi\\times{r}^2\\times h={r * r * target}\\pi$이므로 $h={target}$"
        return q, exp, str(target)
    h0 = random.randint(1, target - 1)  # 원기둥 그릇에 처음부터 들어 있던 물의 높이
    w = target - h0                     # 새로 부은 물이 더해 주는 높이
    r = random.randint(2, 5)
    if level == 2:
        # 높이가 반지름의 절반보다 낮은 납작한 원뿔은 그릇으로 어색하므로 제외한다.
        k = random.choice([k for k in range(1, 5) if (3 * w) % (k * k) == 0 and 2 * (3 * w // (k * k)) >= r * k])
        R, H = r * k, 3 * w // (k * k)
        q = (
            f"밑면의 반지름의 길이가 ${R}$ cm, 높이가 ${H}$ cm인 원뿔 모양의 그릇에 물을 가득 채웠다. "
            f"이 물을 밑면의 반지름의 길이가 ${r}$ cm이고 물이 ${h0}$ cm 높이만큼 들어 있는 "
            "원기둥 모양의 그릇에 모두 부었을 때, 원기둥 모양의 그릇에 담긴 물의 높이를 구하시오."
        )
        exp = (
            f"원뿔의 부피는 $\\frac{{1}}{{3}}\\pi\\times{R}^2\\times{H}={R * R * H // 3}\\pi$입니다.\n\n"
            f"새로 부은 물이 더해 주는 높이를 $h$ cm라 하면 $\\pi\\times{r}^2\\times h={R * R * H // 3}\\pi$이므로 $h={w}$입니다.\n\n"
            f"따라서 물의 높이는 ${h0}+{w}={target}$"
        )
        return q, exp, str(target)

    def make():
        r_ = random.randint(2, 5)
        R1, H1, R2 = random.randint(1, 9), random.randint(2, 12), random.randint(1, 9)
        rest = 3 * r_ * r_ * w - R1 * R1 * H1
        if rest <= 0 or rest % (R2 * R2):
            return None
        H2 = rest // (R2 * R2)
        # 납작한 원뿔(높이가 반지름의 절반 미만)은 그릇으로 어색하므로 제외한다.
        return (r_, R1, H1, R2, H2) if 2 <= H2 <= 20 and 2 * H1 >= R1 and 2 * H2 >= R2 else None

    r, R1, H1, R2, H2 = _roll(
        make,
        lambda s: h0 + Fraction(s[1] ** 2 * s[2] + s[3] ** 2 * s[4], 3 * s[0] ** 2),
        target,
    )
    q = (
        f"밑면의 반지름의 길이가 ${R1}$ cm, 높이가 ${H1}$ cm인 원뿔 모양의 그릇과 "
        f"밑면의 반지름의 길이가 ${R2}$ cm, 높이가 ${H2}$ cm인 원뿔 모양의 그릇에 물을 가득 채웠다. "
        f"이 두 그릇의 물을 밑면의 반지름의 길이가 ${r}$ cm이고 물이 ${h0}$ cm 높이만큼 들어 있는 "
        "원기둥 모양의 그릇에 모두 부었을 때, 원기둥 모양의 그릇에 담긴 물의 높이를 구하시오."
    )
    v1, v2 = R1 * R1 * H1, R2 * R2 * H2
    exp = (
        f"두 원뿔의 부피의 합은 $\\frac{{1}}{{3}}\\pi({R1}^2\\times{H1}+{R2}^2\\times{H2})"
        f"=\\frac{{1}}{{3}}\\pi\\times{v1 + v2}$입니다.\n\n"
        f"새로 부은 물이 더해 주는 높이를 $h$ cm라 하면 $\\pi\\times{r}^2\\times h=\\frac{{1}}{{3}}\\pi\\times{v1 + v2}$이므로 $h={w}$입니다.\n\n"
        f"따라서 물의 높이는 ${h0}+{w}={target}$"
    )
    return q, exp, str(target)


# ---------- 8. 자료의 정리와 해석 ----------
def f_freq_table(target, level):
    """도수분포표에서 모르는 도수 A 구하기 (계급의 수: 하 4, 중 6, 상 8)."""
    n = (4, 6, 8)[level - 1]
    freqs = [random.randint(2, 14) for _ in range(n)]
    pos = random.randrange(n)
    freqs[pos] = target
    total = sum(freqs)
    shown = ", ".join("A" if i == pos else str(v) for i, v in enumerate(freqs))
    q = (
        f"학생 ${total}$명의 기록을 조사하여 계급이 {n}개인 도수분포표를 만들었다. "
        f"각 계급의 도수가 차례로 ${shown}$일 때, $A$의 값을 구하시오."
    )
    known = " + ".join(str(v) for i, v in enumerate(freqs) if i != pos)
    exp = (
        f"도수의 총합은 ${total}$이므로\n\n$${known} + A = {total}$$\n\n따라서 $A={target}$"
    )
    return q, exp, str(target)


def f_relative(target, level):
    """상대도수 (하: 도수 구하기, 중: 다른 계급의 도수, 상: 나머지 계급의 도수)."""
    rates = [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5]
    if level == 1:
        while True:
            r = random.choice(rates)
            N = Fraction(target) / Fraction(str(r))
            if N.denominator == 1:
                break
        N = int(N)
        q = (
            f"도수의 총합이 ${N}$인 도수분포표에서 어떤 계급의 상대도수가 ${r}$일 때, "
            "이 계급의 도수를 구하시오."
        )
        exp = f"(상대도수) = (그 계급의 도수) ÷ (도수의 총합)이므로\n\n$${N}\\times{r}={target}$$"
        return q, exp, str(target)
    if level == 2:
        while True:
            r2 = random.choice(rates)
            N = Fraction(target) / Fraction(str(r2))
            r1 = random.choice([x for x in rates if x != r2])
            if N.denominator == 1 and (N * Fraction(str(r1))).denominator == 1:
                break
        N = int(N)
        d1 = int(N * Fraction(str(r1)))
        q = (
            f"도수분포표에서 도수가 ${d1}$인 계급의 상대도수가 ${r1}$일 때, "
            f"상대도수가 ${r2}$인 계급의 도수를 구하시오."
        )
        exp = (
            f"도수의 총합은 ${d1}\\div{r1}={N}$입니다.\n\n"
            f"따라서 상대도수가 ${r2}$인 계급의 도수는 ${N}\\times{r2}={target}$"
        )
        return q, exp, str(target)

    def make():
        N = random.choice([20, 25, 40, 50, 60, 80, 100, 120, 150, 200])
        rs = [random.choice(rates) for _ in range(3)]
        used = sum(Fraction(str(r)) for r in rs)
        if used >= 1 or any((N * Fraction(str(r))).denominator != 1 for r in rs):
            return None
        return N, rs

    N, rs = _roll(
        make, lambda s: s[0] * (1 - sum(Fraction(str(r)) for r in s[1])), target
    )
    q = (
        f"도수의 총합이 ${N}$이고 계급이 4개인 도수분포표에서 세 계급의 상대도수가 각각 "
        f"${rs[0]}$, ${rs[1]}$, ${rs[2]}$일 때, 나머지 한 계급의 도수를 구하시오."
    )
    exp = (
        f"나머지 한 계급의 상대도수는 $1-({rs[0]}+{rs[1]}+{rs[2]})="
        f"{float(1 - sum(Fraction(str(r)) for r in rs)):g}$입니다.\n\n"
        f"따라서 도수는 ${N}\\times{float(1 - sum(Fraction(str(r)) for r in rs)):g}={target}$"
    )
    return q, exp, str(target)


# ==========================================
# [중2] 교과서 단원별 문제
# ==========================================
# ---------- 1. 유리수와 소수 ----------
def _den_tex(twos, fives, others):
    """분모 2^a × 5^b × (다른 소인수) 를 LaTeX 로."""
    parts = []
    for p, e in ((2, twos), (5, fives)):
        if e:
            parts.append(f"{p}^{{{e}}}" if e > 1 else str(p))
    for p, e in others:
        parts.append(f"{p}^{{{e}}}" if e > 1 else str(p))
    return " \\times ".join(parts)


def _is_finite(a, den):
    """a/den 이 유한소수인가? (기약분수의 분모가 2, 5 뿐인가)"""
    d = den // math.gcd(a, den)
    for p in (2, 5):
        while d % p == 0:
            d //= p
    return d == 1


def f_finite_count(target, level):
    """유한소수가 되도록 하는 자연수 a 의 개수 (하: 분모의 소인수 1개, 중: 2개, 상: 두 분수)."""
    def make_den(prime_sets):
        twos, fives = random.randint(0, 3), random.randint(0, 3)
        if twos + fives == 0:
            twos = 1
        others = prime_sets
        den = 2 ** twos * 5 ** fives
        for p, e in others:
            den *= p ** e
        return twos, fives, others, den

    if level == 1:
        p = random.choice([3, 7, 11, 13])
        twos, fives, others, den = make_den([(p, 1)])
        need = p
        dens = [(twos, fives, others, den)]
    elif level == 2:
        pool = [[(3, 2)], [(3, 1), (7, 1)], [(3, 1), (11, 1)], [(7, 1), (11, 1)]]
        others = random.choice(pool)
        twos, fives, others, den = make_den(others)
        need = 1
        for p, e in others:
            need *= p ** e
        dens = [(twos, fives, others, den)]
    else:
        p1, p2 = random.sample([3, 7, 11], 2)
        dens = [make_den([(p1, 1)]), make_den([(p2, 1)])]
        need = p1 * p2
    M = need * target + random.randint(0, need - 1)
    # 직접 세어서 확인
    count = sum(all(_is_finite(a, d[3]) for d in dens) for a in range(1, M + 1))
    assert count == target
    fr = [f"\\frac{{a}}{{{_den_tex(t_, f_, o_)}}}" for t_, f_, o_, _ in dens]
    if level == 3:
        q = (
            f"두 분수 ${fr[0]}$, ${fr[1]}$를 모두 유한소수로 나타낼 수 있도록 하는 "
            f"${M}$ 이하의 자연수 $a$는 모두 몇 개인지 구하시오."
        )
        rule = (
            f"두 분수가 모두 유한소수가 되려면 $a$는 ${dens[0][2][0][0]}$와 ${dens[1][2][0][0]}$의 "
            f"공배수, 즉 ${need}$의 배수여야 합니다."
        )
    else:
        q = (
            f"분수 ${fr[0]}$를 유한소수로 나타낼 수 있도록 하는 "
            f"${M}$ 이하의 자연수 $a$는 모두 몇 개인지 구하시오."
        )
        rule = (
            "기약분수로 나타냈을 때 분모의 소인수가 2나 5뿐이어야 유한소수가 되므로 "
            f"$a$는 ${need}$의 배수여야 합니다."
        )
    exp = f"{rule}\n\n${M}$ 이하의 ${need}$의 배수는 ${M}\\div{need}$의 몫인 ${target}$개입니다."
    return q, exp, str(target)


def _cycle(a, b):
    """a/b 의 소수 전개: (순환하지 않는 부분, 순환마디) 숫자 리스트. 유한소수는 순환마디가 없다."""
    seen, digits, r = {}, [], a % b
    while r and r not in seen:
        seen[r] = len(digits)
        r *= 10
        digits.append(r // b)
        r %= b
    if r == 0:
        return digits, []
    return digits[: seen[r]], digits[seen[r]:]


def _digit(a, b, n):
    """a/b 의 소수점 아래 n번째 숫자 (n=1,2,...)."""
    pre, cyc = _cycle(a, b)
    if n <= len(pre):
        return pre[n - 1]
    return cyc[(n - len(pre) - 1) % len(cyc)]


_RECUR_FRACS = [
    (a, b)
    for b in (3, 6, 7, 9, 11, 12, 13, 14, 15, 18, 21, 22, 26, 27, 30, 33, 35, 36, 37, 39, 42, 44, 45, 55, 66, 77, 90, 99)
    for a in range(1, b)
    if math.gcd(a, b) == 1 and _cycle(a, b)[1]
]


def f_recur_digits(target, level):
    """순환소수의 숫자의 합 (하: 순환마디, 중: 첫째 자리부터 n번째 자리까지, 상: m번째부터 n번째까지)."""
    def make():
        a, b = random.choice(_RECUR_FRACS)
        if level == 1:
            return a, b, 1, 1
        n = random.randint(4, 12)
        m = 1 if level == 2 else random.randint(3, n - 1)
        return a, b, m, n

    def solve(s):
        a, b, m, n = s
        if level == 1:
            return sum(_cycle(a, b)[1])
        return sum(_digit(a, b, i) for i in range(m, n + 1))

    a, b, m, n = _roll(make, solve, target)
    pre, cyc = _cycle(a, b)
    digits = "".join(str(d) for d in (pre + cyc * 4)[:9])
    fr = f"\\frac{{{a}}}{{{b}}}"
    if level == 1:
        q = f"분수 ${fr}$를 순환소수로 나타낼 때, 순환마디를 이루는 모든 숫자의 합을 구하시오."
        exp = (
            f"${fr}=0.{digits}\\cdots$이므로 순환마디는 ${''.join(map(str, cyc))}$입니다.\n\n"
            f"따라서 순환마디를 이루는 숫자의 합은 ${' + '.join(map(str, cyc))} = {target}$"
        )
    else:
        span = f"첫 번째 자리부터 ${n}$번째 자리까지" if level == 2 else f"${m}$번째 자리부터 ${n}$번째 자리까지"
        q = f"분수 ${fr}$를 소수로 나타낼 때, 소수점 아래 {span}의 숫자의 합을 구하시오."
        seq = [_digit(a, b, i) for i in range(m, n + 1)]
        exp = (
            f"${fr}=0.{digits}\\cdots$이므로 소수점 아래 {span}의 숫자는\n\n"
            f"$${', '.join(map(str, seq))}$$\n\n따라서 합은 ${target}$"
        )
    return q, exp, str(target)


# ---------- 2. 식의 계산 ----------
def _xp(var, e):
    """문자의 거듭제곱. 지수가 1 이면 지수를 쓰지 않는다."""
    return var if e == 1 else f"{var}^{{{e}}}"


def f_exponent_solve(target, level):
    """지수법칙으로 모르는 지수 a 구하기 (하: 한 문자, 중: 두 문자, 상: 곱셈·나눗셈 혼합)."""
    a = target
    m = random.choice([2, 3, 4])
    if level == 1:
        c = random.randint(1, 6)
        N = m * a + c
        lhs = f"(x^{{a}})^{{{m}}} \\times {_xp('x', c)}"
        rhs = _xp("x", N)
        steps = f"$$(x^{{a}})^{{{m}}} \\times {_xp('x', c)} = x^{{{m}a+{c}}}$$\n\n$${m}a+{c}={N}$$"
    elif level == 2:
        c, p, q_ = random.randint(1, 6), random.randint(1, 5), random.randint(1, 5)
        N, M = m * a + c, m * p + q_
        lhs = f"(x^{{a}}{_xp('y', p)})^{{{m}}} \\times {_xp('x', c)}{_xp('y', q_)}"
        rhs = f"{_xp('x', N)}{_xp('y', M)}"
        steps = f"$$x^{{{m}a+{c}}}{_xp('y', M)} = {rhs}$$\n\n$${m}a+{c}={N}$$"
    else:
        d = random.randint(1, min(8, m * a - 1))
        p, q_, n = random.randint(1, 5), random.randint(1, 5), random.choice([2, 3])
        N, M = m * a - d, m * p + n * q_
        ypart = f"y^{{{n}}}" if q_ == 1 else f"(y^{{{q_}}})^{{{n}}}"
        lhs = f"(x^{{a}}{_xp('y', p)})^{{{m}}} \\div {_xp('x', d)} \\times {ypart}"
        rhs = f"{_xp('x', N)}{_xp('y', M)}"
        steps = f"$$x^{{{m}a-{d}}}{_xp('y', M)} = {rhs}$$\n\n$${m}a-{d}={N}$$"
    q = f"다음 등식이 성립하도록 하는 자연수 $a$의 값을 구하시오.\n\n$${lhs} = {rhs}$$"
    exp = f"지수법칙을 이용하여 좌변을 정리하면:\n\n{steps}\n\n따라서 $a={target}$"
    return q, exp, str(target)


def _quad_tex(a, b, c):
    return f"{_coef(a, 'x^{2}')} {_sgn(b)}x {_sgn(c)}".replace("+ 1x", "+ x").replace("- 1x", "- x")


def f_poly_coef(target, level):
    """이차식의 덧셈·뺄셈에서 x의 계수 (하: 두 항짜리 2개, 중: 두 항짜리 3개, 상: 세 항짜리 섞인 3개 + 앞의 수 곱하기).

    식이 한 줄로 읽히는 길이를 넘지 않도록 상에서도 이차식을 4개까지 늘리지 않고, 앞에 곱하는 수로 어렵게 한다."""
    g = 2 if level == 1 else 3
    fulls = [True, False, True] if level == 3 else [False] * g    # 상수항까지 쓰는 세 항짜리인지
    while True:
        ops = ["+"] + [random.choice("+-") for _ in range(g - 1)]
        mults = [random.choice([2, 3]), 1, random.choice([1, 1, 2, 3])] if level == 3 else [1] * g
        polys = [[_nonzero(-5, 5), _nonzero(-9, 9), _nonzero(-9, 9)] for _ in range(g)]
        partial = sum(
            (1 if o == "+" else -1) * m * p[1] for o, m, p in zip(ops[:-1], mults[:-1], polys[:-1])
        )
        need = target - partial
        last_b, rem = divmod(need if ops[-1] == "+" else -need, mults[-1])
        if rem != 0 or not 1 <= abs(last_b) <= 9:
            continue
        polys[-1][1] = last_b
        shown = ""
        for i, (o, m, full, (a, b, c)) in enumerate(zip(ops, mults, fulls, polys)):
            inner = _quad_tex(a, b, c) if full else f"{_coef(a, 'x^{2}')} {_sgn(b)}x".replace("+ 1x", "+ x").replace("- 1x", "- x")
            body = f"({inner})" if m == 1 else f"{m}({inner})"
            shown += body if i == 0 else f" {o} {body}"
        if _vis_len(shown) <= EXPR_MAX_LEN - 2:   # 식이 너무 길어지지 않게
            break
    bs = [p[1] for p in polys]
    gathered = (f"{mults[0]}\\times{_paren(bs[0])}" if mults[0] > 1 else str(bs[0])) + "".join(
        f" {o} " + (f"{m}\\times{_paren(b)}" if m > 1 else _paren(b))
        for o, m, b in zip(ops[1:], mults[1:], bs[1:])
    )
    q = f"다음 식을 계산하였을 때 $x$의 계수를 구하시오.\n\n$${shown}$$"
    exp = f"괄호를 풀고 $x$의 계수만 모으면:\n\n$${gathered} = {target}$$"
    return q, exp, str(target)


# ---------- 3. 부등식 ----------
def f_ineq_cond(target, level):
    """일차부등식의 해가 x > k 일 때 상수 a 구하기 (좌변의 괄호 묶음 수: 하 0, 중 2, 상 3)."""
    d = random.choice([2, 3, 4])
    k = random.randint(-5, 5)
    if level == 1:
        p, q_ = random.randint(3, 7), _nonzero(-9, 9)
        alpha, beta = p, q_
        lhs = _lin(p, q_)
    else:
        groups = level  # 중 2묶음, 상 3묶음
        coefs = [random.randint(2, 4)] + [random.choice([-3, -2, 2, 3]) for _ in range(groups - 1)]
        consts = [_nonzero(-4, 4) for _ in range(groups)]
        alpha = sum(coefs)
        beta = sum(c * s for c, s in zip(coefs, consts))
        if alpha <= 0:
            return f_ineq_cond(target, level)
        lhs = f"{coefs[0]}(x {_sgn(consts[0])})" + "".join(
            f" {'+' if c > 0 else '-'} {abs(c)}(x {_sgn(s)})" for c, s in zip(coefs[1:], consts[1:])
        )
    gamma = alpha - d
    if gamma == 0:
        gamma, d = alpha - (d + 1), d + 1
    d = alpha - gamma
    delta = target + beta + k * d
    rhs = f"{_coef(gamma)} {_sgn(delta)} - a"
    q = (
        f"$x$에 관한 일차부등식 ${lhs} > {rhs}$의 해가 $x>{k}$일 때, 상수 $a$의 값을 구하시오."
    )
    exp = (
        "괄호를 풀고 $x$를 포함한 항은 좌변으로, 나머지는 우변으로 이항하여 정리하면:\n\n"
        f"$${_coef(d)} > {delta - beta} - a$$\n\n"
        f"$$x > \\frac{{{delta - beta} - a}}{{{d}}}$$\n\n"
        f"이 해가 $x>{k}$와 같으므로 $\\frac{{{delta - beta} - a}}{{{d}}}={k}$, 즉 $a={target}$"
    )
    return q, exp, str(target)


def _round_budget(lo, span):
    """lo 보다 크고 lo+span 보다 작은 금액 중, 5000원·1000원 같은 '떨어지는' 값을 되도록 고른다."""
    for step in (500, 100, 50, 10):
        cands = [v for v in range(lo + 1, lo + span) if v % step == 0]
        if cands:
            return random.choice(cands)
    return lo + random.randint(0, span - 1)


def f_ineq_word(target, level):
    """일차부등식의 활용 (하: 포장비 포함, 중: 이미 산 물건 제외, 상: 할인 + 포장비 + 배송비)."""
    if level == 1:
        p, w = random.randint(3, 15) * 100, random.randint(1, 5) * 500
        B = _round_budget(w + p * target, p)
        q = (
            f"한 개에 ${p}$원인 사탕을 포장비 ${w}$원을 내고 포장하여 ${B}$원 이하로 사려고 한다. "
            "사탕을 최대 몇 개까지 살 수 있는지 구하시오."
        )
        exp = (
            f"사탕을 $x$개 산다고 하면 ${p}x+{w}\\le{B}$이므로 $x\\le{_frac(B - w, p)}$입니다.\n\n"
            f"따라서 최대 ${target}$개까지 살 수 있습니다."
        )
        return q, exp, str(target)
    if level == 2:
        p, q_ = random.randint(3, 15) * 100, random.randint(2, 12) * 100
        m = random.randint(2, 6)
        B = _round_budget(p * m + q_ * target, q_)
        q = (
            f"한 개에 ${p}$원인 연필을 이미 ${m}$자루 샀고, 남은 돈으로 한 개에 ${q_}$원인 지우개를 사려고 한다. "
            f"가진 돈이 ${B}$원일 때, 지우개를 최대 몇 개까지 살 수 있는지 구하시오."
        )
        exp = (
            f"지우개를 $x$개 산다고 하면 ${p}\\times{m}+{q_}x\\le{B}$이므로 $x\\le{_frac(B - p * m, q_)}$입니다.\n\n"
            f"따라서 최대 ${target}$개까지 살 수 있습니다."
        )
        return q, exp, str(target)
    while True:
        d = random.choice([10, 20, 25, 30, 40, 50])
        p = random.randint(2, 20) * 100
        price = p * (100 - d) // 100
        break
    w, s = random.randint(1, 5) * 500, random.randint(1, 4) * 500
    B = _round_budget(w + s + price * target, price)
    q = (
        f"정가가 ${p}$원인 물건을 ${d}\\%$ 할인하여 판매한다. 포장비 ${w}$원과 배송비 ${s}$원을 따로 내고 "
        f"${B}$원 이하로 이 물건을 사려고 할 때, 최대 몇 개까지 살 수 있는지 구하시오."
    )
    exp = (
        f"할인한 가격은 ${p}\\times\\frac{{{100 - d}}}{{100}}={price}$원입니다.\n\n"
        f"물건을 $x$개 산다고 하면 ${price}x+{w}+{s}\\le{B}$이므로 $x\\le{_frac(B - w - s, price)}$입니다.\n\n"
        f"따라서 최대 ${target}$개까지 살 수 있습니다."
    )
    return q, exp, str(target)


# ---------- 4. 연립방정식 ----------
def f_sys_word(target, level):
    """연립방정식의 활용 (하: 닭과 토끼, 중: 가격, 상: 거리·속력)."""
    if level == 1:
        r = random.randint(2, 12)
        H, L = target + r, 2 * target + 4 * r
        q = (
            f"닭과 토끼가 모두 ${H}$마리 있고, 다리의 수의 합이 ${L}$개이다. "
            "닭은 몇 마리인지 구하시오."
        )
        exp = (
            "닭을 $x$마리, 토끼를 $y$마리라 하면\n\n"
            f"$$\\begin{{cases}}x + y = {H}\\\\2x + 4y = {L}\\end{{cases}}$$\n\n"
            f"이 연립방정식을 풀면 $x={target}$, $y={r}$입니다."
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            p, q_ = random.randint(3, 15) * 100, random.randint(3, 15) * 100
            if p != q_:
                break
        y = random.randint(2, 12)
        n, M = target + y, p * target + q_ * y
        q = (
            f"한 송이에 ${p}$원인 장미와 한 송이에 ${q_}$원인 카네이션을 합하여 ${n}$송이를 사고 "
            f"${M}$원을 내었더니 거스름돈이 없었다. 장미는 몇 송이를 샀는지 구하시오."
        )
        exp = (
            "장미를 $x$송이, 카네이션을 $y$송이라 하면\n\n"
            f"$$\\begin{{cases}}x + y = {n}\\\\{p}x + {q_}y = {M}\\end{{cases}}$$\n\n"
            f"이 연립방정식을 풀면 $x={target}$, $y={y}$입니다."
        )
        return q, exp, str(target)
    while True:
        v1 = random.choice([d for d in range(2, 10) if target % d == 0])
        v2 = random.randint(v1 + 1, 12)
        y = v2 * random.randint(1, 4)
        break
    D, T = target + y, target // v1 + y // v2
    q = (
        f"${D}$ km의 거리를 처음에는 시속 ${v1}$ km로 걷다가 나머지는 시속 ${v2}$ km로 달렸더니 "
        f"모두 ${T}$시간이 걸렸다. 시속 ${v1}$ km로 걸은 거리는 몇 km인지 구하시오."
    )
    exp = (
        f"시속 ${v1}$ km로 걸은 거리를 $x$ km, 시속 ${v2}$ km로 달린 거리를 $y$ km라 하면\n\n"
        f"$$\\begin{{cases}}x + y = {D}\\\\\\frac{{x}}{{{v1}}} + \\frac{{y}}{{{v2}}} = {T}\\end{{cases}}$$\n\n"
        f"이 연립방정식을 풀면 $x={target}$, $y={y}$입니다."
    )
    return q, exp, str(target)


# ---------- 5. 일차함수와 그래프 ----------
def f_func_intercept(target, level):
    """일차함수의 절편·기울기 (하: x절편으로 y절편, 중: 두 그래프의 절편이 같다, 상: 평행이동한 그래프가 일치)."""
    if level == 1:
        p = random.choice([d for d in range(1, target + 1) if target % d == 0])
        a = -target // p
        if random.random() < 0.5:
            p, a = -p, -a
        q = (
            f"일차함수 $y={_coef(a)} + b$의 그래프의 $x$절편이 ${p}$일 때, $y$절편을 구하시오."
        )
        exp = (
            f"$x$절편이 ${p}$이므로 $y=0$일 때 $x={p}$입니다.\n\n"
            f"$0={a}\\times({p})+b$에서 $b={target}$이므로 $y$절편은 ${target}$입니다."
        )
        return q, exp, str(target)
    if level == 2:
        n = random.choice([d for d in range(1, target + 1) if target % d == 0 and d >= 2 or target == 2])
        c = -target // n
        m = _nonzero(-4, 4)
        if random.random() < 0.5:
            n, c = -n, -c
        q = (
            f"일차함수 $y={_lin(m, c)}$의 그래프의 $y$절편과 일차함수 $y={_coef(n)} + a$의 그래프의 "
            "$x$절편이 서로 같을 때, 상수 $a$의 값을 구하시오."
        )
        exp = (
            f"$y={_lin(m, c)}$의 $y$절편은 ${c}$입니다.\n\n"
            f"$y={_coef(n)} + a$의 $x$절편은 $-\\frac{{a}}{{{n}}}$이므로 $-\\frac{{a}}{{{n}}}={c}$, 즉 $a={target}$"
        )
        return q, exp, str(target)
    k = random.choice([d for d in range(2, 9) if target % d == 0] or [1])
    m = target // k
    while True:
        c, e = _nonzero(-6, 6), _nonzero(-6, 6)
        d = c + e
        if d != 0:
            break
    lead = f"\\frac{{a}}{{{k}}}x" if k > 1 else "ax"
    q = (
        f"일차함수 $y={lead} {_sgn(c)}$의 그래프를 $y$축의 방향으로 ${e}$만큼 평행이동하면 "
        f"일차함수 $y={_lin(m, d)}$의 그래프와 일치한다. 상수 $a$의 값을 구하시오."
    )
    exp = (
        f"평행이동한 그래프의 식은 $y={lead} {_sgn(c)} {_sgn(e)}$이고, 이 그래프가 $y={_lin(m, d)}$와 일치하므로\n\n"
        f"기울기가 같아야 합니다. $\\frac{{a}}{{{k}}}={m}$, 즉 $a={target}$"
        if k > 1
        else f"평행이동한 그래프의 기울기는 변하지 않으므로 $a={m}$입니다."
    )
    return q, exp, str(target)


def f_func_cross(target, level):
    """직선의 교점 (하: x좌표, 중: 일차방정식의 그래프의 교점, 상: 세 직선이 한 점에서 만난다)."""
    if level == 1:
        while True:
            m1, m2 = _nonzero(-4, 4), _nonzero(-4, 4)
            if m1 != m2:
                break
        b1 = _nonzero(-9, 9)
        y0 = m1 * target + b1
        b2 = y0 - m2 * target
        q = (
            f"두 일차함수 $y={_lin(m1, b1)}$, $y={_lin(m2, b2)}$의 그래프의 교점의 $x$좌표를 구하시오."
        )
        exp = (
            f"두 식의 $y$가 같을 때의 $x$의 값이 교점의 $x$좌표이므로\n\n"
            f"$${_lin(m1, b1)} = {_lin(m2, b2)}$$\n\n따라서 $x={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            a1, b1, a2, b2 = _nonzero(-5, 5), _nonzero(-5, 5), _nonzero(-5, 5), _nonzero(-5, 5)
            if a1 * b2 - a2 * b1 != 0:
                break
        y0 = random.randint(-6, 6)
        c1, c2 = a1 * target + b1 * y0, a2 * target + b2 * y0
        e1 = f"{_coef(a1)} {_sgn(b1)}y".replace("+ 1y", "+ y").replace("- 1y", "- y") + f" = {c1}"
        e2 = f"{_coef(a2)} {_sgn(b2)}y".replace("+ 1y", "+ y").replace("- 1y", "- y") + f" = {c2}"
        q = (
            f"두 일차방정식 ${e1}$, ${e2}$의 그래프의 교점의 $x$좌표를 구하시오."
        )
        exp = (
            "두 방정식을 연립하여 풀면 교점의 좌표를 구할 수 있습니다.\n\n"
            f"$$\\begin{{cases}}{e1}\\\\{e2}\\end{{cases}}$$\n\n"
            f"이 연립방정식의 해는 $x={target}$, $y={y0}$입니다."
        )
        return q, exp, str(target)
    while True:
        m1, m2, m3 = random.sample([-4, -3, -2, -1, 1, 2, 3, 4], 3)
        X0 = random.randint(-4, 4)
        y0 = target + m3 * X0
        b1, b2 = y0 - m1 * X0, y0 - m2 * X0
        if b1 != 0 and b2 != 0:
            break
    q = (
        f"세 일차함수 $y={_lin(m1, b1)}$, $y={_lin(m2, b2)}$, $y={_coef(m3)} + k$의 그래프가 "
        "한 점에서 만날 때, 상수 $k$의 값을 구하시오."
    )
    exp = (
        f"앞의 두 직선의 교점은 $({X0}, {y0})$입니다.\n\n"
        f"세 번째 직선도 이 점을 지나므로 ${y0}={m3}\\times({X0})+k$에서 $k={target}$"
    )
    return q, exp, str(target)


# ---------- 6. 삼각형과 사각형의 성질 ----------
def f_pythag(target, level):
    """피타고라스 정리 (하: 직각삼각형 하나, 중: 두 개, 상: 세 개가 이어진 경우의 마지막 변)."""
    n = level + 1
    T2 = target * target
    while True:
        parts = [random.randint(1, T2 - n + 1) for _ in range(n - 1)]
        last = T2 - sum(parts)
        if last >= 1:
            sq = parts + [last]
            break
    sides = [_sqrt_or_int(s) for s in sq]
    if n == 2:
        q = (
            f"$\\angle C=90^\\circ$인 직각삼각형 $ABC$에서 $\\overline{{AC}}={sides[0]}$, "
            f"$\\overline{{BC}}={sides[1]}$일 때, $\\overline{{AB}}$의 길이를 구하시오."
        )
        exp = (
            "피타고라스 정리에 의하여\n\n"
            f"$$\\overline{{AB}}^2=({sides[0]})^2+({sides[1]})^2={sq[0]}+{sq[1]}={T2}$$\n\n"
            f"따라서 $\\overline{{AB}}={target}$"
        )
        return q, exp, str(target)
    tris = ["ABC", "ACD", "ADE"][: n - 1]
    angs = "=".join(f"\\angle {t_}" for t_ in tris)
    seg = ["AB", "BC", "CD", "DE"][:n]
    lens = ", ".join(f"$\\overline{{{s}}}={sd}$" for s, sd in zip(seg, sides))
    word = {3: "두", 4: "세"}[n]
    end = "A" + "ABCDE"[n]
    q = (
        f"${angs}=90^\\circ$인 {word} 직각삼각형 "
        + ", ".join(f"${t_}$" for t_ in tris)
        + f"에서 {lens}일 때, $\\overline{{{end}}}$의 길이를 구하시오."
    )
    lines, total = [], sq[0] + sq[1]
    lines.append(f"$$\\overline{{AC}}^2={sq[0]}+{sq[1]}={total}$$")
    for i in range(2, n):
        nxt = total + sq[i]
        lines.append(f"$$\\overline{{A{'ABCDE'[i + 1]}}}^2={total}+{sq[i]}={nxt}$$")
        total = nxt
    exp = (
        "피타고라스 정리를 차례로 이용하면\n\n" + "\n\n".join(lines)
        + f"\n\n따라서 $\\overline{{{end}}}={target}$"
    )
    return q, exp, str(target)


def _eq_exprs(t, lo, hi):
    """x=t 일 때 같은 값 v(lo~hi)가 되는 두 일차식 (a,b), (c,d)."""
    a, c = random.sample(range(1, 7), 2)
    v = random.randint(lo, hi)
    return (a, v - a * t), (c, v - c * t), v


def f_parallelogram(target, level):
    """평행사변형의 성질 (하: 대변의 길이, 중: 둘레, 상: 대변의 길이 x + 이웃한 각 y)."""
    if level == 1:
        (a, b), (c, d), _ = _eq_exprs(target, 8, 40)
        q = (
            f"평행사변형 $ABCD$에서 $\\overline{{AB}}=({_lin(a, b)})$ cm, "
            f"$\\overline{{DC}}=({_lin(c, d)})$ cm일 때, $x$의 값을 구하시오."
        )
        exp = (
            f"평행사변형의 두 쌍의 대변의 길이는 각각 같으므로\n\n$${_lin(a, b)} = {_lin(c, d)}$$\n\n"
            f"따라서 $x={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            a, c = random.sample(range(1, 6), 2)
            v1, v2 = random.randint(5, 25), random.randint(5, 25)
            b, d = v1 - a * target, v2 - c * target
            e = random.randint(1, 12)
            if e != 2 * (a + c):
                break
        f = 2 * (v1 + v2) - e * target
        q = (
            f"평행사변형 $ABCD$에서 $\\overline{{AB}}=({_lin(a, b)})$ cm, $\\overline{{AD}}=({_lin(c, d)})$ cm이고 "
            f"둘레의 길이가 $({_lin(e, f)})$ cm일 때, $x$의 값을 구하시오."
        )
        exp = (
            "평행사변형의 둘레의 길이는 이웃한 두 변의 길이의 합의 2배이므로\n\n"
            f"$${_lin(e, f)} = 2\\left(({_lin(a, b)}) + ({_lin(c, d)})\\right)$$\n\n"
            f"따라서 $x={target}$"
        )
        return q, exp, str(target)
    x = random.randint(1, target - 1)
    y = target - x
    (a, b), (c, d), _ = _eq_exprs(x, 8, 40)
    (e, f), (g, h) = _pair_supp(y)
    q = (
        f"평행사변형 $ABCD$에서 $\\overline{{AB}}=({_lin(a, b)})$ cm, $\\overline{{DC}}=({_lin(c, d)})$ cm이고, "
        f"$\\angle A=({_lin(e, f, 'y')})^\\circ$, $\\angle B=({_lin(g, h, 'y')})^\\circ$일 때, "
        "$x+y$의 값을 구하시오."
    )
    exp = (
        f"대변의 길이는 같으므로 ${_lin(a, b)}={_lin(c, d)}$, 즉 $x={x}$\n\n"
        f"이웃한 두 각의 크기의 합은 $180^\\circ$이므로 ${_lin(e + g, f + h, 'y')}=180$, 즉 $y={y}$\n\n"
        f"따라서 $x+y={target}$"
    )
    return q, exp, str(target)


_RIGHT_TRIPLES = [(3, 4, 5), (5, 12, 13), (8, 15, 17), (7, 24, 25)]


def f_center(target, level):
    """삼각형의 외심·내심 (하: 직각삼각형의 내접원, 중: 외심, 상: 내심)."""
    if level == 1:
        cands = [(t, (t[0] + t[1] - t[2]) // 2) for t in _RIGHT_TRIPLES]
        cands = [(t, r0) for t, r0 in cands if target % r0 == 0]
        (a0, b0, c0), r0 = random.choice(cands)
        k = target // r0
        a, b, c = a0 * k, b0 * k, c0 * k
        if random.random() < 0.5:
            a, b = b, a
        q = (
            f"$\\angle A=90^\\circ$인 직각삼각형 $ABC$에서 $\\overline{{AB}}={a}$ cm, $\\overline{{AC}}={b}$ cm, "
            f"$\\overline{{BC}}={c}$ cm일 때, 이 삼각형의 내접원의 반지름의 길이를 구하시오."
        )
        exp = (
            f"내접원의 반지름의 길이를 $r$ cm라 하면 삼각형의 넓이에서\n\n"
            f"$$\\frac{{1}}{{2}}\\times{a}\\times{b}=\\frac{{1}}{{2}}\\times r\\times({a}+{b}+{c})$$\n\n"
            f"$$r=\\frac{{{a * b}}}{{{a + b + c}}}={target}$$"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            a, c = random.randint(1, 5), random.randint(1, 5)
            if c != 2 * a:
                break
        v = random.randint(25, 85)
        b, d = v - a * target, 2 * v - c * target
        q = (
            "삼각형 $ABC$의 외심을 $O$라고 할 때, "
            f"$\\angle A=({_lin(a, b)})^\\circ$, $\\angle BOC=({_lin(c, d)})^\\circ$이다. $x$의 값을 구하시오."
        )
        exp = (
            "외심 $O$에 대하여 $\\angle BOC$는 $\\angle A$의 2배이므로\n\n"
            f"$${_lin(c, d)} = 2({_lin(a, b)})$$\n\n따라서 $x={target}$"
        )
        return q, exp, str(target)
    while True:
        B, C = random.randint(35, 90), random.randint(35, 90)
        if (B + C) % 2 == 0 and B + C < 170:
            break
    I = 180 - (B + C) // 2
    a, c, e = random.randint(1, 4), random.randint(1, 4), random.randint(1, 4)
    b, d, f = B - a * target, C - c * target, I - e * target
    q = (
        "삼각형 $ABC$의 내심을 $I$라고 할 때, "
        f"$\\angle B=({_lin(a, b)})^\\circ$, $\\angle C=({_lin(c, d)})^\\circ$, "
        f"$\\angle BIC=({_lin(e, f)})^\\circ$이다. $x$의 값을 구하시오."
    )
    exp = (
        "내심 $I$는 세 내각의 이등분선의 교점이므로 $\\angle IBC=\\frac{1}{2}\\angle B$, "
        "$\\angle ICB=\\frac{1}{2}\\angle C$입니다.\n\n"
        f"$$\\angle BIC=180^\\circ-\\frac{{1}}{{2}}(\\angle B+\\angle C)$$\n\n"
        f"$${_lin(e, f)} = 180 - \\frac{{1}}{{2}}\\left(({_lin(a, b)}) + ({_lin(c, d)})\\right)$$\n\n"
        f"따라서 $x={target}$"
    )
    return q, exp, str(target)


# ---------- 7. 도형의 닮음 ----------
def f_similar(target, level):
    """닮은 도형의 넓이·부피 (하: 닮음비 1:n 두 삼각형의 넓이, 중: 1:2:n 세 원기둥의 부피, 상: 1:2:3:n 네 원기둥의 부피)."""
    if level == 1:
        n = random.choice([2, 3, 4, 5])
        S = target * (1 + n * n)
        shape = random.choice(["삼각형", "사각형", "원", "오각형"])
        q = (
            f"닮음비가 $1:{n}$인 두 닮은 {shape}의 넓이의 합이 ${S}$ cm²일 때, "
            f"작은 {shape}의 넓이를 구하시오."
        )
        exp = (
            f"넓이의 비는 닮음비의 제곱의 비이므로 $1:{n * n}$입니다.\n\n"
            f"작은 {shape}의 넓이는 ${S}\\times\\frac{{1}}{{{1 + n * n}}}={target}$"
        )
        return q, exp, str(target)
    ratios = [1, 2, random.choice([3, 4, 5])] if level == 2 else [1, 2, 3, random.choice([4, 5, 6])]
    total = sum(r ** 3 for r in ratios)
    V = target * total
    word = {3: "세", 4: "네"}[len(ratios)]
    solid = random.choice(["원기둥", "원뿔", "구", "직육면체", "삼각기둥"])
    q = (
        f"닮음비가 ${':'.join(map(str, ratios))}$인 {word} 닮은 {solid}의 부피의 합이 ${V}$ cm³일 때, "
        f"가장 작은 {solid}의 부피를 구하시오."
    )
    exp = (
        f"부피의 비는 닮음비의 세제곱의 비이므로 ${':'.join(str(r ** 3) for r in ratios)}$입니다.\n\n"
        f"가장 작은 {solid}의 부피는 ${V}\\times\\frac{{1}}{{{total}}}={target}$"
    )
    return q, exp, str(target)


def f_parallel_prop(target, level):
    """평행선과 선분의 길이의 비 (하: 삼각형, 중: 평행선 사이, 상: 사다리꼴)."""
    if level == 1:
        while True:
            a, b = random.randint(1, 8), random.randint(1, 8)
            if (a * target) % (a + b) == 0:
                d = a * target // (a + b)
                break
        q = (
            f"삼각형 $ABC$에서 $\\overline{{BC}}\\parallel\\overline{{DE}}$이고 $\\overline{{AD}}={a}$ cm, "
            f"$\\overline{{DB}}={b}$ cm, $\\overline{{DE}}={d}$ cm일 때, $\\overline{{BC}}$의 길이를 구하시오. "
            "(단, 점 $D$, $E$는 각각 변 $AB$, $AC$ 위의 점이다.)"
        )
        exp = (
            f"$\\overline{{AB}}=\\overline{{AD}}+\\overline{{DB}}={a + b}$이고 "
            f"$\\overline{{AB}}:\\overline{{AD}}=\\overline{{BC}}:\\overline{{DE}}$이므로\n\n"
            f"$${a + b}:{a}=x:{d}$$\n\n따라서 $x={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            a, b = random.randint(1, 9), random.randint(1, 9)
            if (a * target) % b == 0:
                d = a * target // b
                break
        q = (
            "서로 평행한 세 직선 $l$, $m$, $n$이 두 직선과 만나는 점을 각각 "
            "$A$, $B$, $C$와 $D$, $E$, $F$라고 하자. "
            f"$\\overline{{AB}}={a}$ cm, $\\overline{{BC}}={b}$ cm, $\\overline{{DE}}={d}$ cm일 때, "
            "$\\overline{EF}$의 길이를 구하시오."
        )
        exp = (
            "평행선 사이에서 선분의 길이의 비는 같으므로 "
            f"$\\overline{{AB}}:\\overline{{BC}}=\\overline{{DE}}:\\overline{{EF}}$\n\n$${a}:{b}={d}:x$$\n\n"
            f"따라서 $x={target}$"
        )
        return q, exp, str(target)
    while True:
        m, n = random.randint(1, 4), random.randint(1, 4)
        u, v = random.randint(1, 30), random.randint(1, 30)
        if u != v and m != n and n * u + m * v == target * (m + n):
            break
    q = (
        f"사다리꼴 $ABCD$에서 $\\overline{{AD}}\\parallel\\overline{{BC}}$, $\\overline{{AD}}={u}$ cm, "
        f"$\\overline{{BC}}={v}$ cm이다. 점 $E$, $F$가 각각 $\\overline{{AB}}$, $\\overline{{DC}}$ 위에 있고 "
        f"$\\overline{{EF}}\\parallel\\overline{{BC}}$, $\\overline{{AE}}:\\overline{{EB}}={m}:{n}$일 때, "
        "$\\overline{EF}$의 길이를 구하시오."
    )
    exp = (
        "점 $A$를 지나 $\\overline{DC}$에 평행한 직선을 그어 $\\overline{EF}$, $\\overline{BC}$와 만나는 점을 "
        "각각 $G$, $H$라고 하면 삼각형의 평행선의 성질에 의하여\n\n"
        f"$$\\overline{{EF}}={u}+({v}-{u})\\times\\frac{{{m}}}{{{m + n}}}=\\frac{{{n}\\times{u}+{m}\\times{v}}}{{{m + n}}}$$\n\n"
        f"따라서 $\\overline{{EF}}={target}$ cm"
    )
    return q, exp, str(target)


# ---------- 8. 경우의 수와 확률 ----------
_BALL_COLORS = ["파란", "빨간", "노란", "초록", "검은"]


def f_prob_unknown(target, level):
    """확률이 주어졌을 때 모르는 공의 개수 x 구하기 (하: 3색 한 번, 중: 4색 한 번 '또는', 상: 5색 두 번 복원추출)."""
    kinds = {1: 2, 2: 3, 3: 4}[level]   # x 를 뺀 색의 수
    counts = [random.randint(1, 8) for _ in range(kinds)]
    N = sum(counts) + target
    if level == 1:
        fr = Fraction(counts[0], N)
    elif level == 2:
        fr = Fraction(counts[0] + target, N)
    else:
        fr = Fraction(counts[0] ** 2, N * N)
    listing = ", ".join(f"{_BALL_COLORS[i]} 공 ${c}$개" for i, c in enumerate(counts))
    shown = _frac(fr.numerator, fr.denominator)
    base = f"주머니 속에 모양과 크기가 같은 {listing}, 흰 공 $x$개가 들어 있다. "
    rest = sum(counts)
    if level == 1:
        q = (
            base + f"이 주머니에서 공 한 개를 임의로 꺼낼 때, 파란 공이 나올 확률이 ${shown}$이다. "
            "$x$의 값을 구하시오."
        )
        exp = (
            f"전체 공의 개수는 ${rest}+x$이므로 파란 공이 나올 확률은 "
            f"$\\frac{{{counts[0]}}}{{{rest}+x}}={shown}$입니다.\n\n${rest}+x={N}$, 즉 $x={target}$"
        )
    elif level == 2:
        q = (
            base + f"이 주머니에서 공 한 개를 임의로 꺼낼 때, 파란 공 또는 흰 공이 나올 확률이 ${shown}$이다. "
            "$x$의 값을 구하시오."
        )
        exp = (
            f"파란 공 또는 흰 공이 나올 확률은 $\\frac{{{counts[0]}+x}}{{{rest}+x}}={shown}$입니다.\n\n"
            f"이 식을 정리하면 $x={target}$"
        )
    else:
        q = (
            base + "공 한 개를 꺼내 확인하고 다시 넣은 후 또 한 개를 꺼낼 때, 두 번 모두 파란 공일 확률이 "
            f"${shown}$이다. $x$의 값을 구하시오."
        )
        exp = (
            f"한 번 꺼낼 때 파란 공이 나올 확률을 $p$라 하면 두 번 모두 파란 공일 확률은 $p^2={shown}$이므로 "
            f"$p=\\frac{{{counts[0]}}}{{{N}}}$입니다.\n\n"
            f"$\\frac{{{counts[0]}}}{{{rest}+x}}=\\frac{{{counts[0]}}}{{{N}}}$에서 $x={target}$"
        )
    return q, exp, str(target)


def f_count_ways(target, level):
    """경우의 수에서 모르는 수 x 구하기 (하: 대표 2명, 중: 회장·부회장, 상: 합과 곱의 법칙)."""
    if level == 1 and target == 2:
        # x=2 이면 '2명 중 2명 뽑기'(1가지)처럼 뜻이 없어지므로 곱의 법칙 문제로 바꾼다.
        if random.random() < 0.5:
            q = (
                "서로 다른 동전 $x$개를 동시에 던질 때, 나올 수 있는 모든 경우의 수가 $4$가지일 때, "
                "$x$의 값을 구하시오."
            )
            exp = "동전 한 개를 던질 때 나오는 경우는 2가지이므로 $x$개를 던지면 $2^x$가지입니다.\n\n$$2^x=4$$\n\n따라서 $x=2$"
        else:
            q = "서로 다른 $x$명을 한 줄로 세우는 방법이 모두 $2$가지일 때, $x$의 값을 구하시오."
            exp = (
                "$x$명을 한 줄로 세우는 방법은 $x\\times(x-1)\\times\\cdots\\times2\\times1$가지이므로\n\n"
                "$$x\\times(x-1)\\times\\cdots\\times2\\times1=2$$\n\n따라서 $x=2$"
            )
        return q, exp, str(target)
    if level == 1:
        N = target * (target - 1) // 2
        q = random.choice(
            [
                f"학생 $x$명 중에서 대표 2명을 뽑는 경우의 수가 ${N}$가지일 때, $x$의 값을 구하시오.",
                f"$x$명이 모두 서로 한 번씩 악수를 하였더니 악수를 모두 ${N}$번 하였다. $x$의 값을 구하시오.",
                f"원 위에 $x$개의 점이 있다. 이 중 두 점을 이어서 만들 수 있는 선분이 모두 ${N}$개일 때, "
                "$x$의 값을 구하시오.",
            ]
        )
        exp = (
            "$x$명(개) 중에서 2명(개)을 뽑는 경우의 수는 $\\frac{x(x-1)}{2}$이므로\n\n"
            f"$$\\frac{{x(x-1)}}{{2}}={N}$$\n\n따라서 $x={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        N = target * (target - 1)
        variants = [
            f"학생 $x$명 중에서 회장 1명, 부회장 1명을 뽑는 경우의 수가 ${N}$가지일 때, $x$의 값을 구하시오.",
            f"서로 다른 $x$권의 책 중에서 2권을 골라 책꽂이에 한 줄로 꽂는 방법이 모두 ${N}$가지일 때, "
            "$x$의 값을 구하시오.",
            f"$x$개의 팀이 있다. 각 팀이 다른 모든 팀과 홈 경기와 원정 경기를 한 번씩 치렀더니 경기가 "
            f"모두 ${N}$번 열렸다. $x$의 값을 구하시오.",
        ]
        if target == 2:
            del variants[1]  # 2권 중에서 2권을 고른다는 말이 되어 어색하다.
        q = random.choice(variants)
        exp = (
            "첫 번째로 고르는 경우가 $x$가지, 그 각각에 대하여 두 번째로 고르는 경우가 $(x-1)$가지이므로\n\n"
            f"$$x(x-1)={N}$$\n\n따라서 $x={target}$"
        )
        return q, exp, str(target)
    b, c, d = random.randint(1, 6), random.randint(2, 6), random.randint(1, 5)
    N = (target + b) * c + d
    q = (
        f"집에서 문구점으로 가는 방법은 버스로 가는 $x$가지와 지하철로 가는 ${b}$가지가 있고, "
        f"문구점에서 도서관으로 가는 방법은 ${c}$가지가 있다. 또, 집에서 도서관으로 문구점을 거치지 않고 "
        f"바로 가는 방법이 ${d}$가지 있다. 집에서 도서관까지 가는 방법이 모두 ${N}$가지일 때, $x$의 값을 구하시오."
    )
    exp = (
        f"문구점을 거쳐 가는 방법은 $(x+{b})\\times{c}$가지이고, 바로 가는 방법이 ${d}$가지이므로\n\n"
        f"$$(x+{b})\\times{c}+{d}={N}$$\n\n따라서 $x={target}$"
    )
    return q, exp, str(target)


# ==========================================
# [중3] 교과서 단원별 문제
# ==========================================
def _lead(a):
    """괄호 앞에 붙는 계수. 1 -> '', -1 -> '-', 3 -> '3'."""
    return "" if a == 1 else ("-" if a == -1 else str(a))


def _tx(n, var="x"):
    """항 하나를 부호와 함께. 3 -> '+ 3x', -1 -> '- x'. 0 이면 빈 문자열."""
    if n == 0:
        return ""
    mag = "" if abs(n) == 1 else str(abs(n))
    return f"{'+' if n > 0 else '-'} {mag}{var}"


# ---------- 1. 실수와 그 계산 ----------
def f_sqrt_count(target, level):
    """조건을 만족시키는 자연수 x 의 개수 (하: a<√x<b 또는 √(N-x), 중: a<√(kx)<b, 상: a<√(kx+c)<b 이고 x 는 m 의 배수)."""
    if level == 1 and random.random() < 0.4:
        N = random.randint(target * target + 1, (target + 1) ** 2)
        cnt = sum(1 for x in range(1, N) if math.isqrt(N - x) ** 2 == N - x)
        assert cnt == target
        q = f"$\\sqrt{{{N}-x}}$가 자연수가 되도록 하는 자연수 $x$는 모두 몇 개인지 구하시오."
        exp = (
            f"$\\sqrt{{{N}-x}}=k$ ($k$는 자연수)로 놓으면 $x={N}-k^2$이고, $x$가 자연수이므로 $k^2<{N}$입니다.\n\n"
            f"이를 만족시키는 자연수 $k$는 $1$부터 ${target}$까지이므로 $x$는 모두 ${target}$개입니다."
        )
        return q, exp, str(target)

    def make():
        a = random.randint(1, 8)
        b = a + random.randint(1, 6)
        lo_closed, hi_closed = random.random() < 0.5, random.random() < 0.5
        k = 1 if level == 1 else random.choice([2, 3, 5])
        c = random.randint(1, 9) if level == 3 else 0
        m = random.choice([2, 3]) if level == 3 else 1
        return a, b, lo_closed, hi_closed, k, c, m

    def solve(s):
        a, b, lo_c, hi_c, k, c, m = s
        cnt = 0
        for x in range(m, b * b + 2, m):
            v = k * x + c
            lo_ok = a * a <= v if lo_c else a * a < v
            hi_ok = v <= b * b if hi_c else v < b * b
            cnt += lo_ok and hi_ok
        return cnt

    a, b, lo_c, hi_c, k, c, m = _roll(make, solve, target)
    root = "x" if (k, c) == (1, 0) else (f"{k}x" if c == 0 else f"{k}x + {c}")
    lo_op = " \\le " if lo_c else " < "
    hi_op = " \\le " if hi_c else " < "
    who = f"${m}$의 배수인 자연수 $x$" if m > 1 else "자연수 $x$"
    q = f"{who}에 대하여 ${a}{lo_op}\\sqrt{{{root}}}{hi_op}{b}$를 만족시키는 $x$는 모두 몇 개인지 구하시오."
    lo_v, hi_v = a * a, b * b
    exp = (
        "각 변을 제곱하면\n\n"
        f"$${lo_v}{lo_op}{root}{hi_op}{hi_v}$$\n\n"
        f"이 부등식을 만족시키는 {who}를 세면 모두 ${target}$개입니다."
    )
    return q, exp, str(target)


def f_surd(target, level):
    """근호가 있는 식 (하: √(a²c) 간단히, 중: 근호의 곱, 상: 근호의 덧셈·뺄셈)."""
    c = random.choice([2, 3, 5, 6, 7, 10])
    if level == 1:
        N = target * target * c
        q = f"$\\sqrt{{{N}}}=a\\sqrt{{{c}}}$일 때, 자연수 $a$의 값을 구하시오."
        exp = f"$\\sqrt{{{N}}}=\\sqrt{{{target}^2\\times{c}}}={target}\\sqrt{{{c}}}$이므로 $a={target}$"
        return q, exp, str(target)
    if level == 2:
        T = target * target * c
        divs = [d for d in range(2, T) if T % d == 0 and d != T // d and T // d >= 2]
        p = random.choice(divs)
        q_ = T // p
        q = f"$\\sqrt{{{p}}}\\times\\sqrt{{{q_}}}=a\\sqrt{{{c}}}$일 때, 자연수 $a$의 값을 구하시오."
        exp = (
            f"$\\sqrt{{{p}}}\\times\\sqrt{{{q_}}}=\\sqrt{{{p}\\times{q_}}}=\\sqrt{{{T}}}"
            f"=\\sqrt{{{target}^2\\times{c}}}={target}\\sqrt{{{c}}}$이므로 $a={target}$"
        )
        return q, exp, str(target)
    while True:
        ops = ["+", random.choice("+-"), random.choice("+-")]
        a1, a2 = random.randint(1, 9), random.randint(1, 9)
        part = a1 + (a2 if ops[1] == "+" else -a2)
        need = target - part
        a3 = need if ops[2] == "+" else -need
        if 1 <= a3 <= 12 and len({a1, a2, a3}) == 3:
            break
    parts = [a1, a2, a3]
    expr = f"\\sqrt{{{a1 * a1 * c}}}" + "".join(
        f" {o} \\sqrt{{{a * a * c}}}" for o, a in zip(ops[1:], parts[1:])
    )
    simple = f"{a1}\\sqrt{{{c}}}" + "".join(
        f" {o} {a}\\sqrt{{{c}}}" for o, a in zip(ops[1:], parts[1:])
    )
    sgn = " ".join(f"{o} {a}" for o, a in zip(ops[1:], parts[1:]))
    q = f"${expr}=k\\sqrt{{{c}}}$일 때, 자연수 $k$의 값을 구하시오."
    exp = (
        "각 근호를 $a\\sqrt{b}$ 꼴로 고치면\n\n"
        f"$${simple}$$\n\n$$=({a1} {sgn})\\sqrt{{{c}}}={target}\\sqrt{{{c}}}$$\n\n"
        f"따라서 $k={target}$"
    )
    return q, exp, str(target)


# ---------- 2. 다항식의 곱셈과 인수분해 ----------
def f_expand_coef(target, level):
    """다항식의 전개에서 계수 (하: (x+p)(x+q)의 x, 중: (ax+b)(cx+d)의 x, 상: 네 일차식의 곱의 x²)."""
    if level == 1:
        while True:
            p = _nonzero(-12, 18)
            qv = target - p
            if qv != 0 and abs(qv) <= 18:
                break
        e = f"(x {_sgn(p)})(x {_sgn(qv)})"
        q = f"다음 식을 전개하였을 때 $x$의 계수를 구하시오.\n\n$${e}$$"
        exp = f"$${e}=x^2 {_sgn(p + qv)}x {_sgn(p * qv)}$$\n\n따라서 $x$의 계수는 ${target}$"
        return q, exp, str(target)
    if level == 2:
        while True:
            a, b, c = random.randint(1, 4), _nonzero(-9, 9), _nonzero(-5, 5)
            if (target - b * c) % a == 0:
                d = (target - b * c) // a
                if d != 0 and abs(d) <= 15:
                    break
        e = f"({_lin(a, b)})({_lin(c, d)})"
        q = f"다음 식을 전개하였을 때 $x$의 계수를 구하시오.\n\n$${e}$$"
        exp = (
            f"$$(ax+b)(cx+d)=acx^2+(ad+bc)x+bd$$\n\n"
            f"이므로 $x$의 계수는 ${a}\\times({d})+({b})\\times({c})={target}$"
        )
        return q, exp, str(target)

    def make():
        return [_nonzero(-5, 5) for _ in range(4)]

    def solve(v):
        return sum(v[i] * v[j] for i in range(4) for j in range(i + 1, 4))

    v = _roll(make, solve, target)
    e = "".join(f"(x {_sgn(p)})" for p in v)
    pairs = " + ".join(f"({v[i]})({v[j]})" for i in range(4) for j in range(i + 1, 4))
    q = f"다음 식을 전개하였을 때 $x^2$의 계수를 구하시오.\n\n$${e}$$"
    exp = (
        "네 일차식의 곱에서 $x^2$항은 상수항 두 개를 곱하고 나머지 두 일차식에서 $x$를 하나씩 택해 만들어지므로 "
        f"$x^2$의 계수는\n\n$${pairs}={target}$$"
    )
    return q, exp, str(target)


def f_factor_ab(target, level):
    """인수분해 (하: (x+p)(x+q) 전개 후 a+b, 중: 공통인수, 상: 잘못 본 계수)."""
    if level == 1:
        def make():
            return _nonzero(-9, 9), _nonzero(-9, 9)

        p, qv = _roll(make, lambda s: (s[0] + s[1]) + s[0] * s[1], target)
        q = (
            f"$x^2+ax+b=(x {_sgn(p)})(x {_sgn(qv)})$일 때, 상수 $a$, $b$에 대하여 $a+b$의 값을 구하시오."
        )
        exp = (
            f"$$(x {_sgn(p)})(x {_sgn(qv)})=x^2 {_tx(p + qv)} {_sgn(p * qv)}$$\n\n"
            f"이므로 $a={p + qv}$, $b={p * qv}$입니다. 따라서 $a+b={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        def make():
            r = random.choice([-4, -3, -2, 2, 3, 4, 5])
            c1, dd = _nonzero(-12, 12), _nonzero(-6, 6)
            if (r * r + c1) % r:
                return None
            return r, c1, dd

        def solve(s):
            r, c1, dd = s
            a = -(r * r + c1) // r
            b = -(r * r + dd * r)
            return a + b

        r, c1, dd = _roll(make, solve, target)
        a = -(r * r + c1) // r
        b = -(r * r + dd * r)
        q = (
            f"두 다항식 $x^2+ax {_sgn(c1)}$, $x^2 {_tx(dd)}+b$의 공통인 인수가 $x {_sgn(-r)}$일 때, "
            "상수 $a$, $b$에 대하여 $a+b$의 값을 구하시오."
        )
        exp = (
            f"$x {_sgn(-r)}$이 인수이므로 $x={r}$을 대입하면 두 식의 값이 모두 $0$입니다.\n\n"
            f"${r * r} {_sgn(a * r)} {_sgn(c1)}=0$에서 $a={a}$\n\n"
            f"${r * r} {_sgn(dd * r)} + b=0$에서 $b={b}$\n\n"
            f"따라서 $a+b={target}$"
        )
        return q, exp, str(target)

    def make():
        return tuple(_nonzero(-9, 9) for _ in range(4))

    def solve(s):
        p, qv, r, w = s
        return (r + w) + p * qv

    p, qv, r, w = _roll(make, solve, target)
    q = (
        "$x^2$의 계수가 $1$인 어떤 이차식을 인수분해하는데, 현우는 $x$의 계수를 잘못 보아 "
        f"$(x {_sgn(p)})(x {_sgn(qv)})$로 인수분해하였고, 지아는 상수항을 잘못 보아 "
        f"$(x {_sgn(r)})(x {_sgn(w)})$로 인수분해하였다. 처음 이차식의 $x$의 계수와 상수항의 합을 구하시오."
    )
    exp = (
        f"현우는 상수항을 바르게 보았으므로 상수항은 $({p})({qv})={p * qv}$입니다.\n\n"
        f"지아는 $x$의 계수를 바르게 보았으므로 $x$의 계수는 ${r}+({w})={r + w}$입니다.\n\n"
        f"따라서 처음 이차식은 $x^2 {_tx(r + w)} {_sgn(p * qv)}$이고, $x$의 계수와 상수항의 합은 ${target}$"
    )
    return q, exp, str(target)


# ---------- 3. 이차방정식 ----------
def f_quad_roots(target, level):
    """이차방정식의 해 (하: 큰 근, 중: 한 근이 주어졌을 때 상수, 상: 중근의 조건)."""
    if level == 1:
        while True:
            r = random.randint(-9, target - 1)
            if r != 0 and r != -target:
                break
        b, c = -(target + r), target * r
        q = f"이차방정식 $x^2 {_tx(b)} {_sgn(c)}=0$의 두 근 중 큰 근을 구하시오."
        exp = (
            f"좌변을 인수분해하면\n\n$$(x {_sgn(-target)})(x {_sgn(-r)})=0$$\n\n"
            f"따라서 $x={target}$ 또는 $x={r}$이고, 큰 근은 ${target}$"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            r = random.choice([-4, -3, -2, -1, 1, 2, 3])
            c = -r * (r + target)
            if c != 0:
                break
        q = (
            f"이차방정식 $x^2+ax {_sgn(c)}=0$의 한 근이 $x={r}$일 때, 상수 $a$의 값을 구하시오."
        )
        exp = (
            f"$x={r}$을 대입하면\n\n$${r * r} + a\\times({r}) {_sgn(c)} = 0$$\n\n"
            f"따라서 $a={target}$"
        )
        return q, exp, str(target)
    alpha, beta = random.randint(1, 3), random.randint(1, 9)
    c = alpha * target + beta
    q = (
        f"이차방정식 $x^2+2({_lin(alpha, beta, 'm')})x+{c * c}=0$이 중근을 가질 때, "
        "양수 $m$의 값을 구하시오."
    )
    exp = (
        "중근을 가지려면 판별식이 $0$이어야 합니다. 즉, 좌변이 완전제곱식이 되어야 하므로\n\n"
        f"$$({_lin(alpha, beta, 'm')})^2={c * c}$$\n\n"
        f"${_lin(alpha, beta, 'm')}={c}$ 또는 ${_lin(alpha, beta, 'm')}=-{c}$이고, 양수인 $m$은 ${target}$"
    )
    return q, exp, str(target)


def f_quad_word(target, level):
    """이차방정식의 활용 (하: 연속하는 두 수의 곱, 중: 십자 모양 길, 상: 두 정사각형의 넓이)."""
    if level == 1:
        kind = random.choice(["자연수", "자연수", "홀수" if target % 2 else "짝수"])
        step = 1 if kind == "자연수" else 2
        ask_small = kind != "자연수" or random.random() < 0.5
        small = target if ask_small else target - step
        N = small * (small + step)
        who = "작은" if ask_small else "큰"
        q = f"연속하는 두 {kind}의 곱이 ${N}$일 때, 두 수 중 {who} 수를 구하시오."
        exp = (
            f"작은 수를 $x$라 하면 큰 수는 $x+{step}$이므로 $x(x+{step})={N}$\n\n"
            f"$x^2 {_tx(step)} - {N}=0$에서 $(x-{small})(x+{small + step})=0$이고 $x>0$이므로 $x={small}$\n\n"
            f"따라서 {who} 수는 ${target}$"
        )
        return q, exp, str(target)
    if level == 2:
        # 길의 폭이 정원의 가로·세로에 비해 지나치게 넓어지지 않도록 3배 이상으로 잡는다.
        W, H = 3 * target + random.randint(2, 20), 3 * target + random.randint(1, 14)
        A = (W - target) * (H - target)
        q = (
            f"가로의 길이가 ${W}$ m, 세로의 길이가 ${H}$ m인 직사각형 모양의 정원에 폭이 일정한 십자 모양의 길을 "
            f"만들려고 한다. 길을 제외한 정원의 넓이가 ${A}$ m²가 되도록 하려면 길의 폭을 몇 m로 해야 하는지 구하시오."
        )
        exp = (
            f"길의 폭을 $x$ m라 하면 길을 제외한 정원은 가로가 $({W}-x)$ m, 세로가 $({H}-x)$ m인 직사각형과 같으므로\n\n"
            f"$$({W}-x)({H}-x)={A}$$\n\n$$x^2-{W + H}x+{W * H - A}=0$$\n\n"
            f"$(x-{target})(x-{W + H - target})=0$에서 $x<{min(W, H)}$이므로 $x={target}$"
        )
        return q, exp, str(target)
    while True:
        L = random.randint(target + 1, 2 * target - 1) if target > 1 else None
        if L:
            break
    S = target * target + (L - target) ** 2
    q = (
        f"길이가 ${L}$ cm인 선분 $AB$ 위에 점 $P$를 잡아 $\\overline{{AP}}$, $\\overline{{BP}}$를 각각 한 변으로 하는 "
        f"정사각형을 만들었다. 두 정사각형의 넓이의 합이 ${S}$ cm²일 때, $\\overline{{AP}}$의 길이를 구하시오. "
        "(단, $\\overline{AP}>\\overline{BP}$)"
    )
    exp = (
        f"$\\overline{{AP}}=x$ cm라 하면 $\\overline{{BP}}=({L}-x)$ cm이므로\n\n"
        f"$$x^2+({L}-x)^2={S}$$\n\n$$2x^2-{2 * L}x+{L * L - S}=0$$\n\n"
        f"$x={target}$ 또는 $x={L - target}$이고 $\\overline{{AP}}>\\overline{{BP}}$이므로 $x={target}$"
    )
    return q, exp, str(target)


# ---------- 4. 이차함수와 그래프 ----------
def f_parabola_a(target, level):
    """이차함수의 식 구하기 (하: y=ax² 가 지나는 점, 중: 꼭짓점과 지나는 점, 상: 일반형의 꼭짓점 p+q)."""
    if level == 1:
        p = random.choice([-4, -3, -2, -1, 1, 2, 3, 4])
        q = f"이차함수 $y=ax^2$의 그래프가 점 $({p}, {target * p * p})$를 지날 때, 상수 $a$의 값을 구하시오."
        exp = f"$y=ax^2$에 $({p}, {target * p * p})$를 대입하면 ${target * p * p}=a\\times({p})^2$이므로 $a={target}$"
        return q, exp, str(target)
    if level == 2:
        p, qv = _nonzero(-5, 5), _nonzero(-9, 9)
        m = p + random.choice([-3, -2, -1, 1, 2, 3])
        n = target * (m - p) ** 2 + qv
        q = (
            f"이차함수 $y=a(x {_sgn(-p)})^2 {_sgn(qv)}$의 그래프가 점 $({m}, {n})$을 지날 때, 상수 $a$의 값을 구하시오."
        )
        exp = (
            f"$({m}, {n})$을 대입하면 ${n}=a({m} {_sgn(-p)})^2 {_sgn(qv)}$이므로\n\n"
            f"${n - qv}=a\\times{(m - p) ** 2}$, 즉 $a={target}$"
        )
        return q, exp, str(target)
    while True:
        a = random.choice([1, 2, 3, -1, -2, -3])
        p = random.randint(-3, 5)
        qv = target - p
        b, c = -2 * a * p, a * p * p + qv
        if b != 0 and c != 0:
            break
    q = (
        f"이차함수 $y={_coef(a, 'x^{2}')} {_tx(b)} {_sgn(c)}$의 그래프의 꼭짓점의 좌표를 $(p, q)$라고 할 때, "
        "$p+q$의 값을 구하시오."
    )
    exp = (
        f"$y=a(x-p)^2+q$ 꼴로 고치면\n\n"
        f"$$y={_lead(a)}(x {_sgn(-p)})^2 {_sgn(qv)}$$\n\n"
        f"이므로 꼭짓점의 좌표는 $({p}, {qv})$이고, $p+q={target}$"
    )
    return q, exp, str(target)


def _expanded(a):
    """a(x-p)^2 + q 를 전개한 꼴(기호 p, q) 문자열."""
    s1 = "-" if a > 0 else "+"
    s2 = "+" if a > 0 else "-"
    pa = "" if abs(a) == 1 else str(abs(a))
    return f"{_coef(a, 'x^{2}')} {s1} {abs(2 * a)}px {s2} {pa}p^2 + q"


def f_parabola_shift(target, level):
    """이차함수의 평행이동 (하: x축 방향으로만 p, 중: 두 방향 p+q, 상: x축 대칭이동 후 평행이동 p+q)."""
    a = random.choice([1, 2, 3, -1, -2, -3])
    if level == 1:
        p = target
        b, c = -2 * a * p, a * p * p
        q = (
            f"이차함수 $y={_coef(a, 'x^{2}')}$의 그래프를 $x$축의 방향으로 $p$만큼 평행이동하였더니 "
            f"이차함수 $y={_coef(a, 'x^{2}')} {_tx(b)} {_sgn(c)}$의 그래프와 일치하였다. $p$의 값을 구하시오."
        )
        exp = (
            f"평행이동한 그래프의 식은 $y={_lead(a)}(x-p)^2$이고, 이것을 전개하면 $y={_expanded(a)}$ 꼴에서 $q=0$입니다.\n\n"
            f"일차항의 계수를 비교하면 ${-2 * a}p={b}$이므로 $p={target}$"
        )
        return q, exp, str(target)
    while True:
        p = random.randint(-3, target + 3)
        qv = target - p
        if p != 0 and qv != 0:
            break
    if level == 2:
        A, B, C = a, -2 * a * p, a * p * p + qv
        if B == 0 or C == 0:
            return f_parabola_shift(target, level)
        q = (
            f"이차함수 $y={_coef(a, 'x^{2}')}$의 그래프를 $x$축의 방향으로 $p$만큼, $y$축의 방향으로 $q$만큼 평행이동하였더니 "
            f"이차함수 $y={_coef(A, 'x^{2}')} {_tx(B)} {_sgn(C)}$의 그래프와 일치하였다. $p+q$의 값을 구하시오."
        )
        exp = (
            f"평행이동한 그래프의 식은 $y={_lead(a)}(x-p)^2+q$이고, 이것을 전개하면 $y={_expanded(a)}$입니다. "
            f"일차항과 상수항의 계수를 비교하면\n\n$${-2 * a}p={B}$$\n\n$${_lead(a)}p^2+q={C}$$\n\n"
            f"에서 $p={p}$, $q={qv}$입니다. 따라서 $p+q={target}$"
        )
        return q, exp, str(target)
    A, B, C = -a, 2 * a * p, -a * p * p + qv
    if B == 0 or C == 0:
        return f_parabola_shift(target, level)
    q = (
        f"이차함수 $y={_coef(a, 'x^{2}')}$의 그래프를 $x$축에 대하여 대칭이동한 후, $x$축의 방향으로 $p$만큼, "
        f"$y$축의 방향으로 $q$만큼 평행이동하였더니 이차함수 $y={_coef(A, 'x^{2}')} {_tx(B)} {_sgn(C)}$의 그래프와 "
        "일치하였다. $p+q$의 값을 구하시오."
    )
    exp = (
        f"$x$축에 대하여 대칭이동하면 $y={_coef(A, 'x^{2}')}$이고, 평행이동하면 $y={_lead(A)}(x-p)^2+q$입니다. "
        f"이것을 전개하면 $y={_expanded(A)}$이므로 일차항과 상수항의 계수를 비교하면\n\n"
        f"$${-2 * A}p={B}$$\n\n$${_lead(A)}p^2+q={C}$$\n\n"
        f"에서 $p={p}$, $q={qv}$입니다. 따라서 $p+q={target}$"
    )
    return q, exp, str(target)


# ---------- 5. 삼각비 ----------
def f_trig_length(target, level):
    """삼각비의 활용 (하: 삼각형의 넓이, 중: 평행사변형의 넓이, 상: 수선의 발이 만드는 선분의 길이)."""
    if level == 1:
        pairs = [
            (a, 4 * target // a)
            for a in range(1, 4 * target + 1)
            if (4 * target) % a == 0 and a <= 30 and 4 * target // a <= 30
        ]
        a, b = random.choice(pairs)
        ang = random.choice([30, 150])
        q = (
            f"$\\overline{{AB}}={a}$ cm, $\\overline{{AC}}={b}$ cm, $\\angle A={ang}^\\circ$인 삼각형 $ABC$의 넓이를 구하시오."
        )
        exp = (
            f"$\\triangle ABC=\\frac{{1}}{{2}}\\times{a}\\times{b}\\times\\sin {ang}^\\circ"
            f"=\\frac{{1}}{{2}}\\times{a}\\times{b}\\times\\frac{{1}}{{2}}={target}$ (cm²)"
        )
        return q, exp, str(target)
    if level == 2:
        pairs = [
            (a, 2 * target // a)
            for a in range(1, 2 * target + 1)
            if (2 * target) % a == 0 and a <= 30 and 2 * target // a <= 30
        ]
        a, b = random.choice(pairs)
        ang = random.choice([30, 150])
        q = (
            f"$\\overline{{AB}}=\\overline{{CD}}={a}$ cm, $\\overline{{BC}}=\\overline{{DA}}={b}$ cm, "
            f"$\\angle B=\\angle D={ang}^\\circ$인 평행사변형 $ABCD$의 넓이를 구하시오."
        )
        exp = (
            f"평행사변형의 넓이는 $ab\\sin\\theta$이므로 ${a}\\times{b}\\times\\sin {ang}^\\circ"
            f"={a}\\times{b}\\times\\frac{{1}}{{2}}={target}$ (cm²)"
        )
        return q, exp, str(target)
    A, B, C, D = random.choice([("A", "B", "C", "D"), ("P", "Q", "R", "S"), ("X", "Y", "Z", "T")])
    q = (
        f"$\\angle {C}=90^\\circ$, $\\angle {B}=30^\\circ$인 직각삼각형 ${A}{B}{C}$에서 $\\overline{{{A}{C}}}={2 * target}$ cm이다. "
        f"점 ${C}$에서 $\\overline{{{A}{B}}}$에 내린 수선의 발을 ${D}$라고 할 때, $\\overline{{{A}{D}}}$의 길이를 구하시오."
    )
    exp = (
        f"$\\angle {A}=90^\\circ-30^\\circ=60^\\circ$이고, 직각삼각형 ${A}{C}{D}$에서\n\n"
        f"$$\\overline{{{A}{D}}}=\\overline{{{A}{C}}}\\cos 60^\\circ={2 * target}\\times\\frac{{1}}{{2}}={target}$$"
    )
    return q, exp, str(target)


# ---------- 6. 원의 성질 ----------
def f_circle_angle(target, level):
    """원주각 (하: 중심각 = 2×원주각, 중: 내접사각형의 대각, 상: 둘을 함께)."""
    def central_pair(t):
        while True:
            a, c = random.randint(1, 5), random.randint(1, 5)
            if c != 2 * a:
                break
        v = random.randint(25, 85)
        return (a, v - a * t), (c, 2 * v - c * t)

    def inscribed_pair(t):
        a, c = random.sample(range(1, 6), 2)
        v = random.randint(50, 130)
        return (a, v - a * t), (c, 180 - v - c * t)

    if level == 1:
        (a, b), (c, d) = central_pair(target)
        q = (
            "원 $O$에서 호 $AB$에 대한 원주각의 크기가 "
            f"$({_lin(a, b)})^\\circ$, 중심각의 크기가 $({_lin(c, d)})^\\circ$일 때, $x$의 값을 구하시오."
        )
        exp = (
            "한 호에 대한 중심각의 크기는 원주각의 크기의 2배이므로\n\n"
            f"$${_lin(c, d)} = 2({_lin(a, b)})$$\n\n따라서 $x={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        (a, b), (c, d) = inscribed_pair(target)
        q = (
            "원에 내접하는 사각형 $ABCD$에서 "
            f"$\\angle A=({_lin(a, b)})^\\circ$, $\\angle C=({_lin(c, d)})^\\circ$일 때, $x$의 값을 구하시오."
        )
        exp = (
            "원에 내접하는 사각형의 마주 보는 두 각의 크기의 합은 $180^\\circ$이므로\n\n"
            f"$${_lin(a + c, b + d)} = 180$$\n\n따라서 $x={target}$"
        )
        return q, exp, str(target)
    x = random.randint(1, target - 1)
    y = target - x
    (a, b), (c, d) = central_pair(x)
    (e, f), (g, h) = inscribed_pair(y)
    q = (
        "원 $O$에서 호 $AB$에 대한 원주각의 크기가 "
        f"$({_lin(a, b)})^\\circ$, 중심각의 크기가 $({_lin(c, d)})^\\circ$이다. 또, 이 원에 내접하는 사각형 $PQRS$에서 "
        f"$\\angle P=({_lin(e, f, 'y')})^\\circ$, $\\angle R=({_lin(g, h, 'y')})^\\circ$일 때, $x+y$의 값을 구하시오."
    )
    exp = (
        f"중심각의 크기는 원주각의 크기의 2배이므로 ${_lin(c, d)}=2({_lin(a, b)})$, 즉 $x={x}$\n\n"
        f"내접사각형의 대각의 크기의 합은 $180^\\circ$이므로 ${_lin(e + g, f + h, 'y')}=180$, 즉 $y={y}$\n\n"
        f"따라서 $x+y={target}$"
    )
    return q, exp, str(target)


def f_circle_length(target, level):
    """원의 현·접선의 길이 (하: 현의 수직이등분선, 중: 외접사각형, 상: 내접원의 접선의 길이)."""
    if level == 1:
        d = random.randint(1, 12)
        r2 = d * d + target * target
        q = (
            f"반지름의 길이가 $\\sqrt{{{r2}}}$인 원 $O$의 중심에서 현 $AB$에 내린 수선의 발을 $M$이라고 하자. "
            f"$\\overline{{OM}}={d}$일 때, $\\overline{{AM}}$의 길이를 구하시오."
        ) if math.isqrt(r2) ** 2 != r2 else (
            f"반지름의 길이가 ${math.isqrt(r2)}$인 원 $O$의 중심에서 현 $AB$에 내린 수선의 발을 $M$이라고 하자. "
            f"$\\overline{{OM}}={d}$일 때, $\\overline{{AM}}$의 길이를 구하시오."
        )
        exp = (
            f"직각삼각형 $OAM$에서 $\\overline{{AM}}^2=\\overline{{OA}}^2-\\overline{{OM}}^2={r2}-{d * d}={target * target}$이므로\n\n"
            f"$\\overline{{AM}}={target}$"
        )
        return q, exp, str(target)
    if level == 2:
        while True:
            ab, bc = random.randint(3, 20), random.randint(3, 20)
            cd = target + bc - ab
            if cd >= 2:
                break
        q = (
            f"원 $O$에 외접하는 사각형 $ABCD$에서 $\\overline{{AB}}={ab}$ cm, $\\overline{{BC}}={bc}$ cm, "
            f"$\\overline{{CD}}={cd}$ cm일 때, $\\overline{{AD}}$의 길이를 구하시오."
        )
        exp = (
            "원에 외접하는 사각형에서 마주 보는 두 변의 길이의 합은 같으므로\n\n"
            f"$$\\overline{{AB}}+\\overline{{CD}}=\\overline{{BC}}+\\overline{{AD}}$$\n\n"
            f"$${ab}+{cd}={bc}+\\overline{{AD}}$$\n\n따라서 $\\overline{{AD}}={target}$ cm"
        )
        return q, exp, str(target)
    while True:
        b = random.randint(1, target - 1)
        c = target - b
        a = random.randint(1, 15)
        if b >= 1 and c >= 1:
            break
    q = (
        "삼각형 $ABC$의 내접원이 세 변 $AB$, $BC$, $CA$와 만나는 점을 각각 $P$, $Q$, $R$라고 하자. "
        f"$\\overline{{AP}}={a}$ cm, $\\overline{{BQ}}={b}$ cm, $\\overline{{CR}}={c}$ cm일 때, $\\overline{{BC}}$의 길이를 구하시오."
    )
    exp = (
        "원 밖의 한 점에서 그 원에 그은 두 접선의 길이는 같으므로 $\\overline{BP}=\\overline{BQ}$, "
        "$\\overline{CQ}=\\overline{CR}$입니다.\n\n"
        f"$$\\overline{{BC}}=\\overline{{BQ}}+\\overline{{CQ}}=\\overline{{BQ}}+\\overline{{CR}}={b}+{c}={target}$$ (cm)"
    )
    return q, exp, str(target)


# ---------- 7. 통계 ----------
def _devs(n, total_sq):
    """합이 0 이고 제곱의 합이 total_sq 인 정수 n 개 (편차)."""
    bound = min(7, math.isqrt(total_sq))
    for _ in range(20000):
        ds = [random.randint(-bound, bound) for _ in range(n - 2)]
        s = -sum(ds)
        q = total_sq - sum(d * d for d in ds)
        disc = 2 * q - s * s
        if disc < 0:
            continue
        r = math.isqrt(disc)
        if r * r != disc or (s + r) % 2:
            continue
        ds += [(s + r) // 2, (s - r) // 2]
        if any(ds) and max(abs(d) for d in ds) <= 12:
            return ds
    return None


def _inline_list(values):
    """수 목록을 '$3$, $5$, ...' 꼴로. 한 줄에 다 안 들어가면 글처럼 줄이 바뀐다. (수식 하나로 쓰면 휴대전화에서 옆으로 넘친다.)"""
    return ", ".join(f"${v}$" for v in values)


def f_variance(target, level):
    """자료의 분산 (변량의 개수: 하 6, 중 8, 상 12)."""
    n = (6, 8, 12)[level - 1]
    while True:
        ds = _devs(n, target * n)
        if ds:
            break
    mean = random.randint(max(-min(ds) + 1, 3), max(-min(ds) + 1, 3) + 12)
    data = [mean + d for d in ds]
    random.shuffle(data)
    q = f"다음 자료의 분산을 구하시오.\n\n{_inline_list(data)}"
    total_sq = sum(d * d for d in ds)
    exp = (
        f"(평균) $=\\frac{{{sum(data)}}}{{{n}}}={mean}$이므로 편차는\n\n{_inline_list([v - mean for v in data])}\n\n"
        f"편차의 제곱은 {_inline_list([(v - mean) ** 2 for v in data])}이고, 그 합은 ${total_sq}$이므로\n\n"
        f"(분산) $=\\frac{{{total_sq}}}{{{n}}}={target}$"
    )
    return q, exp, str(target)


def f_center_value(target, level):
    """대푯값 (하: 평균, 중: 중앙값, 상: 중앙값과 최빈값의 합)."""
    if level == 1:
        n = 5
        while True:
            vals = [random.randint(1, 30) for _ in range(n - 1)]
            last = target * n - sum(vals)
            if 1 <= last <= 30:
                vals.append(last)
                break
        random.shuffle(vals)
        q = f"다음 자료의 평균을 구하시오.\n\n{_inline_list(vals)}"
        exp = f"(평균) $=\\frac{{{' + '.join(map(str, vals))}}}{{{n}}}=\\frac{{{sum(vals)}}}{{{n}}}={target}$"
        return q, exp, str(target)
    if level == 2:
        n = 7
        while True:
            low = sorted(random.randint(1, target) for _ in range(3))
            high = sorted(random.randint(target, target + 25) for _ in range(3))
            vals = low + [target] + high
            if len(set(vals)) >= 5:
                break
        shown = vals[:]
        random.shuffle(shown)
        q = f"다음 자료의 중앙값을 구하시오.\n\n{_inline_list(shown)}"
        exp = (
            f"자료를 크기순으로 나열하면\n\n{_inline_list(vals)}\n\n"
            f"변량이 {n}개이므로 중앙값은 $4$번째 값인 ${target}$"
        )
        return q, exp, str(target)

    def make():
        hi = random.choice([4, 6, 10, 14, 20])
        return [random.randint(1, hi) for _ in range(9)]

    def solve(vals):
        s = sorted(vals)
        cnt = {}
        for v in vals:
            cnt[v] = cnt.get(v, 0) + 1
        top = max(cnt.values())
        modes = [v for v, c in cnt.items() if c == top]
        if len(modes) != 1 or top < 2:
            return None
        return s[len(s) // 2] + modes[0]

    vals = _roll(make, solve, target)
    s = sorted(vals)
    cnt = {}
    for v in vals:
        cnt[v] = cnt.get(v, 0) + 1
    mode = max(cnt, key=lambda v: cnt[v])
    median = s[len(s) // 2]
    q = f"다음 자료의 중앙값과 최빈값의 합을 구하시오.\n\n{_inline_list(vals)}"
    exp = (
        f"자료를 크기순으로 나열하면\n\n{_inline_list(s)}\n\n"
        f"변량이 9개이므로 중앙값은 $5$번째 값인 ${median}$이고, 가장 많이 나타나는 값인 최빈값은 ${mode}$입니다.\n\n"
        f"따라서 ${median}+{mode}={target}$"
    )
    return q, exp, str(target)


# ==========================================
# 학년별 단원과 단원마다 쓸 문제 유형
#  - 교과서 목차 순서. 앞의 12가지(g1_linear 등 기존 문제)는 알맞은 단원의 유형으로 그대로 쓴다.
# ==========================================
UNIT_FAMILIES = {
    1: [
        ("소인수분해", [f_gcd, f_divcount]),
        ("정수와 유리수", [f_intsum, f_mixed]),
        (
            "문자와 식",
            [
                _levels(g1_linear, g1_linear_mid, g1_linear_high),
                _levels(g1_expr, g1_expr_mid, g1_expr_high),
                _levels(g1_prop, g1_prop_mid, g1_prop_high),
                f_linexpr,
            ],
        ),
        ("좌표평면과 그래프", [f_prop, f_area]),
        ("기본 도형과 작도", [f_line_angle, f_parallel_angle]),
        ("평면도형의 성질", [f_polygon_angle, f_sector]),
        ("입체도형의 성질", [f_cuboid_cut, f_pour]),
        ("자료의 정리와 해석", [f_freq_table, f_relative]),
    ],
    2: [
        ("유리수와 소수", [f_finite_count, f_recur_digits]),
        (
            "식의 계산",
            [_levels(g2_exp, g2_exp_mid, g2_exp_high), f_exponent_solve, f_poly_coef],
        ),
        ("부등식", [_levels(g2_ineq, g2_ineq_mid, g2_ineq_high), f_ineq_cond, f_ineq_word]),
        ("연립방정식", [_levels(g2_sys, g2_sys_mid, g2_sys_high), f_sys_word]),
        (
            "일차함수와 그래프",
            [_levels(g2_func, g2_func_mid, g2_func_high), f_func_intercept, f_func_cross],
        ),
        ("삼각형과 사각형의 성질", [f_pythag, f_parallelogram, f_center]),
        ("도형의 닮음", [f_similar, f_parallel_prop]),
        ("경우의 수와 확률", [f_prob_unknown, f_count_ways]),
    ],
    3: [
        ("실수와 그 계산", [_levels(g3_sqrt, g3_sqrt_mid, g3_sqrt_high), f_sqrt_count, f_surd]),
        (
            "다항식의 곱셈과 인수분해",
            [_levels(g3_advanced, g3_advanced_mid, g3_advanced_high), f_expand_coef, f_factor_ab],
        ),
        ("이차방정식", [f_quad_roots, f_quad_word]),
        (
            "이차함수와 그래프",
            [_levels(g3_quad_func, g3_quad_func_mid, g3_quad_func_high), f_parabola_a, f_parabola_shift],
        ),
        ("삼각비", [_levels(g3_trigo, g3_trigo_mid, g3_trigo_high), f_trig_length]),
        ("원의 성질", [f_circle_angle, f_circle_length]),
        (
            "통계",
            [_levels(g1_stat, g1_stat_mid, g1_stat_high), f_variance, f_center_value],
        ),
    ],
}
GRADE_TITLES = {1: "중학교 1학년 수학", 2: "중학교 2학년 수학", 3: "중학교 3학년 수학"}
DIFFICULTY_LABELS = {1: "하", 2: "중", 3: "상"}


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
        problems.append((question, f"*({unit} 단원)*\n\n{explanation}", answer))

    return title, problems


# ==========================================
# Streamlit 메인 로직 및 GUI 구성
# ==========================================
st.set_page_config(page_title="중생대 문제", layout="centered")

# 새로 접속한 세션이면 방문자 수를 1 올린다. (버튼을 눌러 스크립트가 다시 실행돼도 한 번만 센다.)
if "visit_number" not in st.session_state:
    st.session_state.visit_number = record_visit()

# 모든 화면에서 Streamlit 자체 버튼(상단 툴바·메뉴, Manage app 등)을 숨긴다.
hide_streamlit_chrome()

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
st.session_state.setdefault("button_click_count", 0)
st.session_state.setdefault("blue_watch_clue_shown", False)
st.session_state.setdefault(
    "blue_watch_clue_target", random.randint(1, BLUE_WATCH_CLUE_MAX_CLICK)
)
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
        .intro-card {{
            position: relative;
            width: 90%;
            max-width: 800px;
            aspect-ratio: 16 / 9;
            margin: 40px auto 20px auto;
            background-image: url("https://i.postimg.cc/657sHLjT/Agent-Image-A-plain-flat-gray-background-completely-filling-the-frame-overlaid-with-a-subtle-natu.png");
            background-size: contain;
            background-repeat: no-repeat;
            background-position: center;
            border-radius: 12px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.6);
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 30px;
            box-sizing: border-box;
        }}
        .visitor-count {{
            margin-top: 28px;
            text-align: center;
            color: #7b8190;
            font-size: 12px !important;
            letter-spacing: 1px;
        }}
        .intro-text {{
            color: #1a1a1a !important;
            font-size: 24px !important;
            font-weight: bold !important;
            letter-spacing: 2px;
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
        {NEXT_BUTTON_SHIFT_CSS}
        </style>
        """,
        unsafe_allow_html=True,
    )

    text_placeholder = st.empty()
    full_text = "임시 텍스트입니다"

    if not st.session_state.get("intro_text_done", False):
        current_text = ""
        for char in full_text:
            current_text += char
            text_placeholder.markdown(
                f"<div class='intro-card'><span class='intro-text'>{current_text}</span></div>",
                unsafe_allow_html=True,
            )
            time.sleep(0.15)
        st.session_state.intro_text_done = True
    else:
        text_placeholder.markdown(
            f"<div class='intro-card'><span class='intro-text'>{full_text}</span></div>",
            unsafe_allow_html=True,
        )

    # 버튼 오른쪽 정렬을 위해 st.columns 사용
    st.write("")
    col1, col2, col3 = st.columns([6, 2, 2])
    with col3:
        if lucky_button("다음으로", use_container_width=True):
            # 화면에는 보이지 않는 스톱워치를 여기서 시작한다. (끝은 마지막 화면의 "다음으로")
            st.session_state.stopwatch_start = time.monotonic()
            st.session_state.step = "select_difficulty"
            st.rerun()

    # 이번 접속이 몇 번째 방문인지 작게 보여 준다. (저장에 실패해 번호가 없으면 숨긴다.)
    if SHOW_VISITOR_COUNT and st.session_state.visit_number:
        st.markdown(
            f"<div class='visitor-count'>{st.session_state.visit_number:,}번째 방문자</div>",
            unsafe_allow_html=True,
        )

# --- 화면 0-1: 난이도 선택 (인트로와 같은 검은 배경, 상·중·하 버튼 3개) ---
#  - 시작 화면부터 이 화면까지는 스토커 단서가 나오지 않는 구간이다. (BLUE_WATCH_CLUE_EXEMPT_STEPS)
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
            margin: 110px 0 36px 0;
            text-align: center;
            color: #f1f1f1;
            font-size: 26px;
            letter-spacing: 2px;
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
        """,
        unsafe_allow_html=True,
    )

    # (표시 이름, 난이도 값): 상=3, 중=2, 하=1. 값이 클수록 문제가 어렵다.
    cols = st.columns(3)
    for col, (label, level) in zip(cols, [("상", 3), ("중", 2), ("하", 1)]):
        if lucky_button(label, use_container_width=True, dg=col):
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
                    st.error("오답입니다. 정답을 다시 확인해보세요.")

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
        /* 텍스트 창과 버튼은 조선시대 그림 위가 아니라 검은 배경 위에 둔다.
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
            position: relative;
            width: 90%;
            max-width: 800px;
            aspect-ratio: 16 / 9;
            margin: 60px auto 20px auto;
            background-image: url("https://i.postimg.cc/d7D5pjGh/Agent-Image-An-aged-weathered-sheet-of-thick-traditional-Korean-mulberry-paper-hanji-from-the-J.png");
            background-size: contain;
            background-repeat: no-repeat;
            background-position: center;
            border-radius: 12px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.6);
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 30px;
            box-sizing: border-box;
        }}
        .arrival-text {{
            color: #000000 !important;
            font-size: 24px !important;
            font-weight: bold !important;
            letter-spacing: 2px;
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
        {NEXT_BUTTON_SHIFT_CSS}
        </style>
        """,
        unsafe_allow_html=True,
    )

    text_placeholder = st.empty()
    full_text = "임시 텍스트입니다"

    if not st.session_state.get("arrival_text_done", False):
        current_text = ""
        for char in full_text:
            current_text += char
            text_placeholder.markdown(
                f"<div class='arrival-card'><span class='arrival-text'>{current_text}</span></div>",
                unsafe_allow_html=True,
            )
            time.sleep(0.15)
        st.session_state.arrival_text_done = True
    else:
        text_placeholder.markdown(
            f"<div class='arrival-card'><span class='arrival-text'>{full_text}</span></div>",
            unsafe_allow_html=True,
        )

    if st.session_state.arrival_text_done:
        st.write("")
        col1, col2, col3 = st.columns([6, 2, 2])
        with col3:
            if lucky_button("다음으로", use_container_width=True):
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
        .block-container {
            padding-top: 1.5rem !important;
            padding-bottom: 1rem !important;
        }
        iframe {
            margin-bottom: 0px !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # id="prisonIntroBlock"를 붙여 두면, 아래 임베드된 게임 안에서 용의자(스토커) 화면으로
    # 넘어갔을 때 이 안내문을 JS로 찾아 감출 수 있다.
    st.markdown(
        """
        <div id="prisonIntroBlock">
        <h2>🔑 마지막 관문: 감옥 열쇠</h2>
        <p>우물의 물길을 고친 뒤 정신을 차려보니, 낯선 감옥에 갇혀 있습니다. 일곱 개의 열쇠가 걸려 있지만 아직은 만질 수조차 없습니다. 먼저 게임 화면의 파이프 퍼즐을 끝까지 연결해 물길을 완성하고, 그 상태로 감옥으로 돌아와야만 열쇠를 시도해 볼 수 있습니다.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 스톱워치: 처음 "다음으로"를 누른 뒤 지금까지 흐른 시간을 게임 화면에 넘겨 준다.
    # 화면 내용(srcdoc)이 다시 실행될 때마다 바뀌면 게임이 처음부터 다시 로드되므로, 한 번만 만들어 둔다.
    if "prison_key_html" not in st.session_state:
        started = st.session_state.get("stopwatch_start", time.monotonic())
        elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
        st.session_state.prison_key_html = PRISON_KEY_PUZZLE_HTML.replace("__ELAPSED_BEFORE_MS__", str(elapsed_ms))
    st.iframe(st.session_state.prison_key_html, height=820)

# --- 비밀의 방: 어떤 버튼이든 25만분의 1 확률로 누르면 도착하는 화면 ---
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
        </style>
        <div class="secret-room">
            <div class="sr-title">비밀의 방</div>
            <div class="sr-text">
                방금 누른 버튼이 25만분의 1의 확률을 뚫었습니다.<br>
                이곳에 도착한 것은 아주 드문 일입니다.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.balloons()

    # 감옥 퍼즐 화면에서 이곳으로 왔다면 돌아갈 때 게임이 새로 로드되므로, 그때의 스톱워치 값을 다시 만든다.
    st.session_state.pop("prison_key_html", None)

    # 비밀의 방 안의 버튼은 다시 주사위를 굴리지 않는 일반 버튼이다.
    _, mid_col, _ = st.columns([1, 1, 1])
    with mid_col:
        if st.button("돌아가기", use_container_width=True):
            st.session_state.step = st.session_state.get(
                "secret_room_prev_step", "select_grade"
            )
            st.rerun()

# --- 단서 화면(녹색 장갑·파란색 시계 공용): 검은 화면에 단서를 보여 준 뒤 원래 화면으로 자동 복귀 ---
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
    clue_text = html_escape(st.session_state.get("clue_text", STALKER_CLUE_TEXT))
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
            "<h2>중생대 문제</h2><p>문제를 풀 학년을 선택하세요.</p>",
            gap_below=True,
        )

        col1, col2, col3 = st.columns(3)
        if lucky_button("중1 문제 선택", use_container_width=True, dg=col1):
            st.session_state.title, st.session_state.problems = (
                generate_grade_problems(1, st.session_state.difficulty)
            )
            st.session_state.solved_indices = set()
            reset_stalker_clue()
            st.session_state.step = "select_problem"
            st.rerun()
        if lucky_button("중2 문제 선택", use_container_width=True, dg=col2):
            st.session_state.title, st.session_state.problems = (
                generate_grade_problems(2, st.session_state.difficulty)
            )
            st.session_state.solved_indices = set()
            reset_stalker_clue()
            st.session_state.step = "select_problem"
            st.rerun()
        if lucky_button("중3 문제 선택", use_container_width=True, dg=col3):
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
            "<p>풀고 싶은 문제를 고르세요.</p>"
        )
        st.markdown("---")

        cols = st.columns(3)
        for i in range(3):
            ans = st.session_state.problems[i][2]
            is_solved = i in st.session_state.solved_indices

            with cols[i]:
                if is_solved:
                    st.markdown(
                        f"""
                        <div style="
                            background-color: rgba(255, 255, 255, 0.85);
                            border: 2px solid #2e7d32;
                            border-radius: 10px;
                            padding: 12px 8px;
                            text-align: center;
                            box-shadow: 0px 4px 10px rgba(0,0,0,0.2);
                        ">
                            <div style="color: #2e7d32; font-size: 13px; font-weight: bold; margin-bottom: 4px;">✅ 문제 {i+1} 완료</div>
                            <div style="color: #000000; font-size: 18px; font-weight: bold;">정답: {ans}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    if lucky_button(
                        f"문제 {i+1} 📝",
                        key=f"prob_btn_{i}",
                        use_container_width=True,
                    ):
                        # 무작위로 뽑힌 '단서 버튼'을 처음 누르면 문제 대신 검은 단서 화면으로 간다.
                        if is_stalker_clue_button(i):
                            go_stalker_clue()
                        st.session_state.current_problem = (
                            st.session_state.problems[i]
                        )
                        st.session_state.current_problem_idx = i
                        st.session_state.step = "solve"
                        st.rerun()

        if len(st.session_state.solved_indices) == 3:
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
            f"<h3>{html_escape(st.session_state.title)}</h3>\n\n<hr>\n\n{current_q}"
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
                    st.error("암호가 올바르지 않습니다.")

        st.write("")
        col1, col2, col3 = st.columns([6, 2, 2])
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
                    st.error("오답입니다. 다시 풀어보세요!")

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
