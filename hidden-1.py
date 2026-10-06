import random
import time
from html import escape as html_escape
import streamlit as st

# 타임머신 카운트업 애니메이션 재생 시간(ms)
TIMEMACHINE_ANIM_DURATION_MS = 4500

# 앱 전체에서 공통으로 사용할 폰트 (굴림체)
APP_FONT_FAMILY = "'굴림', Gulim, sans-serif"

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
    if clicked and _secret_rng.randrange(SECRET_ROOM_ODDS) == 0:
        go_secret_room()
    return clicked


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
#  - 문제 3개를 모두 풀어야 타임머신에 갈 수 있어서 버튼 3개를 모두 누르게 되므로,
#    단서는 한 판에 정확히 한 번, 반드시 나온다.
# ==========================================
STALKER_CLUE_TEXT = "스토커는 녹색 장갑을 끼고 있었다"
STALKER_CLUE_SECONDS = 10


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


def go_stalker_clue():
    """검은 단서 화면으로 넘어간다. 한 판에 한 번만 보여 준다."""
    st.session_state.stalker_clue_shown = True
    st.session_state.step = "stalker_clue"
    st.rerun()


# ==========================================
# 잘 안 보이는 글씨에 흰색 상자 깔기 (공룡 배경 그림 위의 화면들)
#  - readable_box()      : 제목·안내문처럼 짧은 글을 HTML 로 넘기면 흰 상자 안에 보여 준다.
#  - readable_markdown() : 수식($...$)이 섞인 마크다운(문제·풀이)을 흰 상자 안에 보여 준다.
#  - 오답/정답 알림(st.error · st.success)과 체크박스는 위젯이라 CSS 로 흰 바탕을 깐다.
#  - 상자 모양을 바꾸고 싶으면 아래 READABLE_TEXT_CSS 의 .readable-box 만 고치면 된다.
#  - 이 CSS 는 배경 그림이 있는 화면(문제 선택·풀이·정답, 타임머신 입력)의 <style> 안에 넣어 쓴다.
#    검은 배경 화면(인트로·감옥 열쇠·비밀의 방·단서 화면)에는 넣지 않는다.
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


def readable_box(inner_html):
    """짧은 글(제목·안내문 등)을 흰색 상자 안에 보여 준다. inner_html 은 HTML 이다.

    HTML 안에 빈 줄이 끼면 상자가 끊기므로, 공백·줄바꿈은 한 칸으로 합쳐서 넘긴다.
    """
    one_line = " ".join(str(inner_html).split())
    st.markdown(
        f'<div class="readable-box">{one_line}</div>',
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
  .clue-item{
    display:flex; align-items:center; flex-wrap:wrap; gap:8px;
    font-size:.94rem; line-height:1.5; color:var(--parchment);
  }
  .clue-num{
    flex:none; display:inline-flex; align-items:center; justify-content:center;
    min-width:46px; padding:2px 8px; border-radius:8px;
    background:rgba(212,175,55,.18); border:1px solid rgba(212,175,55,.5);
    font-size:.72rem; font-weight:800; letter-spacing:.02em; color:var(--gold-bright); white-space:nowrap;
  }
  .clue-item b{
    color:#1a0d08; font-weight:800; padding:1px 8px; border-radius:6px;
    background:linear-gradient(180deg,#fce080 0%,#d4af37 100%);
    box-shadow:0 1px 3px rgba(0,0,0,.4);
  }
  .suspect-row{ display:flex; justify-content:center; gap:16px; width:100%; }
  .suspect-btn{
    flex:1; max-width:150px; display:flex; flex-direction:column; align-items:center; gap:8px;
    background:rgba(20,12,8,.55); border:1px solid rgba(140,90,60,.45); border-radius:16px;
    padding:22px 10px 16px; cursor:pointer; transition:transform .15s ease, border-color .15s ease, background .15s ease;
  }
  .suspect-btn:hover, .suspect-btn:focus-visible{ transform:translateY(-4px); border-color:var(--gold); background:rgba(30,18,10,.72); outline:none; }
  .suspect-btn:active{ transform:translateY(-1px) scale(.98); }
  .suspect-svg{ width:82px; height:auto; filter:drop-shadow(0 4px 8px rgba(0,0,0,.6)); margin-bottom:4px; }
  .suspect-id{ font-size:.98rem; color:#e7cfa8; letter-spacing:.03em; font-weight:800; }

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
      btn.innerHTML = `${buildSuspectSVG(s)}<span class="suspect-id">용의자 ${s.id}</span>`;
      btn.addEventListener('click', ()=>{
        audio.init();
        if(s.id === STALKER_ID){
          showToast('스토커를 찾아냈다!', 'success');
          audio.playRotate();
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
      if(introEl) introEl.style.display = (name === 'suspects') ? 'none' : '';
    }catch(e){ /* 크로스 오리진 등으로 접근이 막히면 무시 */ }
  }

  let currentView = 'prison';
  function applyView(name, opts){
    opts = opts || {};
    currentView = name;
    document.getElementById('prisonView').hidden = name !== 'prison';
    document.getElementById('suspectsView').hidden = name !== 'suspects';
    document.getElementById('gameView').hidden = name !== 'game';
    syncParentIntroVisibility(name);
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
    a = random.randint(-5, 5)
    b = random.choice([2, 3, 4])
    d = random.choice([3, 5])

    while ((target + a) * d) % b != 0:
        a += 1
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


def generate_grade_problems(grade):
    if grade == 1:
        funcs = [g1_linear, g1_expr, g1_prop, g1_stat]
        title = "[ 중학교 1학년 수학 ]"
    elif grade == 2:
        funcs = [g2_sys, g2_func, g2_exp, g2_ineq]
        title = "[ 중학교 2학년 수학 ]"
    else:
        funcs = [g3_trigo, g3_quad_func, g3_sqrt, g3_advanced]
        title = "[ 중학교 3학년 수학 ]"

    targets = [15, 9, 2]
    random.shuffle(targets)

    problems = []
    for target in targets:
        func = random.choice(funcs)
        problems.append(func(target))

    return title, problems


# ==========================================
# Streamlit 메인 로직 및 GUI 구성
# ==========================================
st.set_page_config(page_title="중생대 문제", layout="centered")

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
        if lucky_button("다음으로 이동", use_container_width=True):
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
        .stApp {{
            background-image: url("https://i.postimg.cc/J04WRcYy/Agent-Image-A-lively-traditional-Korean-market-street-in-1592-during-the-Joseon-dynasty-lined-wi.png");
            background-size: cover;
            background-position: center;
            background-repeat: no-repeat;
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
            if lucky_button("다음으로 이동", use_container_width=True):
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

    st.iframe(PRISON_KEY_PUZZLE_HTML, height=820)

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

    # 비밀의 방 안의 버튼은 다시 주사위를 굴리지 않는 일반 버튼이다.
    _, mid_col, _ = st.columns([1, 1, 1])
    with mid_col:
        if st.button("돌아가기", use_container_width=True):
            st.session_state.step = st.session_state.get(
                "secret_room_prev_step", "select_grade"
            )
            st.rerun()

# --- 스토커 단서 화면: 검은 화면에 단서를 보여 준 뒤 문제 선택 화면으로 자동 복귀 ---
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
            align-items: center;
            justify-content: center;
            padding: 24px;
            box-sizing: border-box;
            background: #000000;
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
        <div class="stalker-clue"><span class="stalker-clue-text">{STALKER_CLUE_TEXT}</span></div>
        """,
        unsafe_allow_html=True,
    )

    # 정해진 시간 동안 검은 화면을 보여 준 뒤, 문제 선택 화면으로 돌아간다.
    time.sleep(STALKER_CLUE_SECONDS)
    st.session_state.step = "select_problem"
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
        readable_box("<h2>중생대 문제</h2><p>문제를 풀 학년을 선택하세요.</p>")

        col1, col2, col3 = st.columns(3)
        if lucky_button("중1 문제 선택", use_container_width=True, dg=col1):
            st.session_state.title, st.session_state.problems = (
                generate_grade_problems(1)
            )
            st.session_state.solved_indices = set()
            reset_stalker_clue()
            st.session_state.step = "select_problem"
            st.rerun()
        if lucky_button("중2 문제 선택", use_container_width=True, dg=col2):
            st.session_state.title, st.session_state.problems = (
                generate_grade_problems(2)
            )
            st.session_state.solved_indices = set()
            reset_stalker_clue()
            st.session_state.step = "select_problem"
            st.rerun()
        if lucky_button("중3 문제 선택", use_container_width=True, dg=col3):
            st.session_state.title, st.session_state.problems = (
                generate_grade_problems(3)
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
