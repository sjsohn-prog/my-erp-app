import streamlit as st
import pandas as pd
from jinja2 import Environment
import os
import base64
import json
import re
import time
import io
import threading
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
import google.generativeai as genai
from PIL import Image
from streamlit.runtime.scriptrunner import add_script_run_ctx
import pymupdf  # fitz API 경고 방지용 최신 PyMuPDF

# ==========================================
# C-Library (WeasyPrint) 안전 예외 처리
# ==========================================
HAS_WEASYPRINT = True
try:
    from weasyprint import HTML
except Exception:
    HAS_WEASYPRINT = False

# 구글 시트 연동 라이브러리 예외 처리
try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False

# 스레드 동시성 파일 제어용 락(Lock) 객체
file_access_lock = threading.Lock()

# ==========================================
# 0. 보안 비밀번호 및 환경 설정 함수 (최상단 배치)
# ==========================================
def get_secret(key, default=""):
    try:
        if key in st.secrets: return st.secrets[key]
    except Exception: pass
    return default

KEY_FILE = "gemini_key.txt"
DEFAULT_GEMINI_KEY = get_secret("GEMINI_API_KEY", "")

def load_saved_key():
    try:
        if "GEMINI_API_KEY" in st.secrets: return st.secrets["GEMINI_API_KEY"]
    except Exception: pass
    if DEFAULT_GEMINI_KEY.strip(): return DEFAULT_GEMINI_KEY.strip()
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "r", encoding="utf-8") as f: return f.read().strip()
    return ""

gemini_key = load_saved_key()

ADMIN_PASSWORD = get_secret("ADMIN_PASSWORD", "admin0915")
SAVE_PASSWORD = get_secret("SAVE_PASSWORD", "0915")

FLAG_OPTIONS = [
    "Panama", "Liberia", "Marshall Islands", "Hong Kong", "Singapore", 
    "Korea (KR)", "Bahamas", "Malta", "Cyprus", "India", "China", "Greece", "UK"
]

CLASS_OPTIONS = [
    "ABS", "BV", "CCS", "CRS", "DNV", "IRS", "KR", "LR", 
    "NK", "PRS", "RINA", "TL", "Non-IACS", "KR & NK", "DNV & LR", "IRS & DNV", "Panama / KR"
]

CURRENCY_OPTIONS = [
    "KRW", "USD", "EUR", "JPY", "CNY", "SGD", "GBP", "HKD", "AED"
]

STATUS_OPTIONS = [
    "🟡 Quoted", "🔵 PO Received", "🟣 Invoiced", "🟢 Paid", "🔴 Cancelled", "⚪ Draft"
]

GOOGLE_CLIENT_ID = get_secret("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = get_secret("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = get_secret("REDIRECT_URI")
ALLOWED_DOMAIN = get_secret("ALLOWED_DOMAIN", "1solution.co.kr")

doc_db_cols = [
    "IssueDate", "DocDate", "DocType", "OurRef", "YourRef", 
    "ShipName", "TargetName", "Currency", "TotalAmount", "ItemCount", "CreatedBy", "Status"
]

item_master_cols = [
    "ItemName", "PartNo", "Description", "Supplier", "BuyPrice", "ListPrice", "Currency", "Remarks"
]

partner_db_cols = [
    "PartnerName", "PartnerType", "PIC", "Email", "Phone", "DefaultPayment", "DefaultValidity", "Remarks"
]

vessel_db_cols = [
    "ShipName", "IMONo", "Flag", "Class", "Owner", "Remarks"
]

OUR_DB_FILE = "our_db.csv"
CUSTOMER_DB_FILE = "customer_db.csv"
ITEM_MASTER_FILE = "item_master.csv"
PARTNER_DB_FILE = "partner_db.csv"
VESSEL_DB_FILE = "vessel_db.csv"
DRAFTS_FILE = "user_drafts.json"
HISTORY_FILE = "master_history.json"
INPUT_DOCS_DIR = "input_docs"

os.makedirs("output", exist_ok=True)
os.makedirs(INPUT_DOCS_DIR, exist_ok=True)

# ==========================================
# 0-1. 공통 헬퍼 함수 & 실시간 환율 & 구글 시트 연동
# ==========================================
def get_kst_now():
    return datetime.now(timezone(timedelta(hours=9)))

def clean_str(val):
    if pd.isna(val) or val is None: return ""
    s = str(val).strip()
    return "" if s.lower() in ['nan', 'none', 'null', '<na>', 'nan.0', 'none.0'] else s

def clean_df(df):
    if df is None or df.empty: return df
    df = df.copy()
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].apply(clean_str)
    return df

def ensure_cols(df, target_cols):
    if df is None or df.empty:
        return pd.DataFrame(columns=target_cols)
    df = df.copy()
    for col in target_cols:
        if col not in df.columns:
            df[col] = "-"
    return df[target_cols]

def extract_items_list(res):
    if isinstance(res, list): return res
    if isinstance(res, dict):
        for k in ["items", "data", "records", "rows", "parts", "materials", "documents", "headers", "result"]:
            if k in res and isinstance(res[k], list): return res[k]
        for v in res.values():
            if isinstance(v, list): return v
        if any(k in res for k in ["PartNo", "ItemName", "IssueDate", "DocType", "OurRef"]):
            return [res]
    return []

@st.cache_data(ttl=1800)
def get_exchange_rates():
    fallback_rates = {
        "USD": 1.0, "KRW": 1350.0, "EUR": 0.92, "JPY": 150.0,
        "CNY": 7.2, "SGD": 1.35, "GBP": 0.79, "HKD": 7.8, "AED": 3.67
    }
    fetch_time = get_kst_now().strftime("%H:%M:%S")
    urls = [
        f"https://open.er-api.com/v6/latest/USD?_={int(time.time())}",
        "https://api.exchangerate-api.com/v4/latest/USD"
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=4) as response:
                data = json.loads(response.read().decode('utf-8'))
                rates = data.get("rates", {})
                if rates:
                    for c in CURRENCY_OPTIONS:
                        if c not in rates: rates[c] = fallback_rates.get(c, 1.0)
                    return rates, fetch_time
        except Exception: continue
    return fallback_rates, fetch_time

def safe_float(val, default=0.0):
    if val is None or pd.isna(val): return default
    s = str(val).replace(',', '').strip()
    match = re.search(r"[-+]?\d*\.\d+|\d+", s)
    if match:
        try: return float(match.group())
        except ValueError: return default
    return default

def get_currency_symbol(code):
    c = clean_str(code).upper()
    symbols = {
        "USD": "$", "KRW": "₩", "EUR": "€", "JPY": "¥",
        "CNY": "¥", "SGD": "S$", "GBP": "£", "HKD": "HK$", "AED": "AED "
    }
    return symbols.get(c, f"{c} " if c else "")

def get_gsheet_client():
    if not HAS_GSPREAD: return None
    try:
        if "gcp_service_account" in st.secrets:
            scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
            return gspread.authorize(creds)
    except Exception: pass
    return None

def safe_read_csv(filepath, default_cols=None):
    if default_cols is None: default_cols = []
    sheet_title = os.path.splitext(os.path.basename(filepath))[0]
    gc = get_gsheet_client()
    spreadsheet_key = get_secret("SPREADSHEET_KEY")
    
    if gc and spreadsheet_key:
        try:
            sh = gc.open_by_key(spreadsheet_key)
            ws = sh.worksheet(sheet_title)
            data = ws.get_all_records()
            if isinstance(data, list): return ensure_cols(pd.DataFrame(data), default_cols)
        except Exception: pass

    with file_access_lock:
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            return pd.DataFrame(columns=default_cols)
        try:
            df = pd.read_csv(filepath)
            return ensure_cols(df, default_cols)
        except Exception:
            return pd.DataFrame(columns=default_cols)

def safe_save_csv(df, filepath, default_cols=None):
    if default_cols is None: default_cols = []
    cleaned_df = ensure_cols(clean_df(df), default_cols)
    
    with file_access_lock:
        cleaned_df.to_csv(filepath, index=False)
    
    sheet_title = os.path.splitext(os.path.basename(filepath))[0]
    gc = get_gsheet_client()
    spreadsheet_key = get_secret("SPREADSHEET_KEY")
    if gc and spreadsheet_key:
        try:
            sh = gc.open_by_key(spreadsheet_key)
            try: ws = sh.worksheet(sheet_title)
            except Exception: ws = sh.add_worksheet(title=sheet_title, rows="1000", cols="20")
            
            values_to_write = [cleaned_df.columns.values.tolist()] + cleaned_df.fillna("").values.tolist()
            ws.update(values_to_write, 'A1')
        except Exception as e:
            st.warning(f"⚠️ 구글 시트 동기화 주의 (로컬 CSV에 저장됨): {e}")

# ==========================================
# 0-2. 세션 콜백 및 안전 스마트 위젯
# ==========================================
def set_session_val(key, val):
    st.session_state[key] = val

def render_unified_input(label, current_val, base_options, key_prefix):
    txt_key = f"{key_prefix}_txt"
    curr = clean_str(current_val)

    if txt_key not in st.session_state:
        st.session_state[txt_key] = curr

    opts = [clean_str(x) for x in base_options if clean_str(x)]
    opts = list(dict.fromkeys(opts))

    if opts:
        col_in, col_pop = st.columns([0.88, 0.12])
        with col_in:
            st.text_input(f"▾ {label}", key=txt_key)
        with col_pop:
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            with st.popover("📋", help=f"{label} 추천/이력 선택"):
                st.caption(f"**{label} 추천 목록**")
                for idx, opt in enumerate(opts):
                    st.button(
                        opt, 
                        key=f"{key_prefix}_pop_{idx}_{opt[:10]}", 
                        on_click=set_session_val, 
                        args=(txt_key, opt)
                    )
    else:
        st.text_input(f"▾ {label}", key=txt_key)

    return st.session_state.get(txt_key, "")

def format_date_str(raw_str):
    s = re.sub(r'[^0-9]', '', str(raw_str))
    if len(s) == 6:
        return f"20{s[:2]}-{s[2:4]}-{s[4:6]}"
    elif len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return str(raw_str)

def render_date_input(label, current_val, key_prefix):
    txt_key = f"{key_prefix}_date_str"
    
    if txt_key not in st.session_state or not st.session_state[txt_key]:
        raw_c = clean_str(current_val) or get_kst_now().strftime("%Y-%m-%d")
        st.session_state[txt_key] = format_date_str(raw_c)

    col_in, col_pop = st.columns([0.88, 0.12])
    with col_in:
        user_val = st.text_input(f"📅 {label}", key=txt_key)
        formatted_val = format_date_str(user_val)
        if formatted_val != user_val:
            st.session_state[txt_key] = formatted_val

    with col_pop:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        with st.popover("📅", help=f"{label} 달력에서 선택"):
            try:
                init_d = datetime.strptime(st.session_state[txt_key], "%Y-%m-%d").date()
            except Exception:
                init_d = get_kst_now().date()
            picked = st.date_input("날짜 선택", value=init_d, key=f"{key_prefix}_dp")
            if picked:
                picked_str = picked.strftime("%Y-%m-%d")
                if picked_str != st.session_state[txt_key]:
                    st.session_state[txt_key] = picked_str

    return st.session_state.get(txt_key, "")

# ==========================================
# 0-3. 계정별 임시저장(Draft) 전용 제어 함수
# ==========================================
def load_user_draft(user_email):
    if not user_email or not os.path.exists(DRAFTS_FILE): return None
    try:
        with file_access_lock:
            with open(DRAFTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get(user_email)
    except Exception: return None

def save_user_draft(user_email, doc_info, doc_items, visible_cols=None):
    if not user_email: return
    try:
        with file_access_lock:
            data = {}
            if os.path.exists(DRAFTS_FILE):
                try:
                    with open(DRAFTS_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception: data = {}
            
            items_dict = doc_items.to_dict(orient="records") if isinstance(doc_items, pd.DataFrame) else doc_items
            data[user_email] = {
                "doc_info": doc_info,
                "doc_items": items_dict,
                "visible_cols": visible_cols or ["ItemName", "PartNo", "Description", "Qty", "UnitPrice", "Amount", "Remarks"],
                "updated_at": get_kst_now().strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(DRAFTS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception: pass

# ==========================================
# 0-4. i18n 다국어 사전
# ==========================================
TRANSLATIONS = {
    "KR": {
        "subtitle": "사내 임직원 전용 서류 및 자재 관리 시스템",
        "google_login": "🔑 Google 계정으로 로그인",
        "logout": "🚪 로그아웃",
        "user_label": "👤 접속자:",
        "sys_menu": "🖥️ SYSTEM MENU",
        "menu_gen": "서류 파이프라인 Master",
        "menu_db_master": "📦 통합 DB 마스터",
        "menu_history": "서류 이력 & 갤러리",
        "menu_admin": "🛠️ 관리자 메뉴",
        "doc_gen_title": "📄 서류 분석 및 자동 생성 Master System",
        "doc_gen_desc": "매출 및 매입 서류를 AI 파싱으로 자동 생성하며, 1초 서류 승계 기능으로 데이터 입력을 최소화합니다.",
        "ai_expander_title": "⚡ AI 문서 자동 분석 (클릭하여 열기) 🔽",
        "ai_mode_label": "AI 분석 엔진 선택",
        "mode_flash": "⚡ Gemini 3.6 Flash (고속)",
        "mode_thinking": "🧠 Gemini 3.6 Flash (사고)",
        "upload_doc_label": "문서 및 파일 업로드 (PDF, JPG, PNG, XLSX, CSV)",
        "btn_ai_parse": "✨ AI 문서 분석",
        "btn_reset": "🔄 입력 초기화",
        "hdr_title": "📌 {doc_type} 헤더 입력 (모든 항목 직접 입력 가능)",
        "items_title": "📦 품목 상세 내역 (줄바꿈/엔터 지원 / 열 너비 자동 맞춤)",
        "remarks_title": "📝 Remarks & Deviations",
        "reg_title": "📌 DB 데이터 등록 및 저장",
        "pwd_save_label": "🔒 비밀번호",
        "preview_title": "⚡ 실시간 PDF 문서 미리보기",
        "btn_download_pdf": "💾 완성된 PDF 다운로드",
        "filter_category": "1️⃣ 필터 항목 선택",
        "filter_value": "2️⃣ 하위 값 선택",
        "filter_keyword": "🔎 키워드 통합 검색",
        "filter_keyword_ph": "검색어 입력...",
        "total_records": "**총 `{count}` 건 조회됨** (전체 `{total}` 건 중)",
        "btn_download_csv": "📥 필터링된 결과 엑셀(CSV) 다운로드",
        "no_ledger": "등록된 내역이 없습니다.",
        "pwd_admin_label": "관리자 비밀번호 입력",
        "pwd_err": "❌ 비밀번호가 올바르지 않습니다.",
        "all": "전체",
    },
    "EN": {
        "subtitle": "In-house Document & Material Management System",
        "google_login": "🔑 Sign in with Google",
        "logout": "🚪 Logout",
        "user_label": "👤 User:",
        "sys_menu": "🖥️ SYSTEM MENU",
        "menu_gen": "Doc Pipeline Master",
        "menu_db_master": "📦 Unified DB Master",
        "menu_history": "Document Gallery & History",
        "menu_admin": "🛠️ Admin Menu",
        "doc_gen_title": "📄 Document Pipeline Master System",
        "doc_gen_desc": "Supports Outbound/Inbound document creation with 1-click succession and AI parsing.",
        "ai_expander_title": "⚡ AI Document Auto-Analysis (Click to Expand) 🔽",
        "ai_mode_label": "Select AI Engine",
        "mode_flash": "⚡ Gemini 3.6 Flash (Fast)",
        "mode_thinking": "🧠 Gemini 3.6 Flash (Thinking)",
        "upload_doc_label": "Upload Document/Data (PDF, JPG, PNG, XLSX, CSV)",
        "btn_ai_parse": "✨ Analyze Document",
        "btn_reset": "🔄 Reset",
        "hdr_title": "📌 {doc_type} Header Details",
        "items_title": "📦 Line Item Details",
        "remarks_title": "📝 Remarks & Deviations",
        "reg_title": "📌 Save Data to DB",
        "pwd_save_label": "🔒 Password",
        "preview_title": "⚡ Live PDF Document Preview",
        "btn_download_pdf": "💾 Download PDF Document",
        "filter_category": "1️⃣ Select Filter Column",
        "filter_value": "2️⃣ Select Sub-value",
        "filter_keyword": "🔎 Search Keyword",
        "filter_keyword_ph": "Type keyword...",
        "total_records": "**Total `{count}` record(s) found** (Out of `{total}`)",
        "btn_download_csv": "📥 Download Filtered Excel (CSV)",
        "no_ledger": "No records found.",
        "pwd_admin_label": "Enter Admin Password",
        "pwd_err": "❌ Incorrect password.",
        "all": "All",
    }
}

def t(key, **kwargs):
    lang = st.session_state.get('lang', 'KR')
    text = TRANSLATIONS.get(lang, TRANSLATIONS['KR']).get(key, key)
    return text.format(**kwargs) if kwargs else text

# ==========================================
# 0-5. 구글 OAuth 로그인
# ==========================================
def get_google_auth_url():
    if not GOOGLE_CLIENT_ID or not REDIRECT_URI: return None
    scopes = ["https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile", "openid"]
    params = {
        "client_id": GOOGLE_CLIENT_ID.strip(), "redirect_uri": REDIRECT_URI.strip(),
        "response_type": "code", "scope": " ".join(scopes), "access_type": "offline", "prompt": "select_account"
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"

def get_google_user_info(code):
    token_url = "https://oauth2.googleapis.com/token"
    payload = urllib.parse.urlencode({
        "code": code, "client_id": GOOGLE_CLIENT_ID.strip(), "client_secret": GOOGLE_CLIENT_SECRET.strip(),
        "redirect_uri": REDIRECT_URI.strip(), "grant_type": "authorization_code"
    }).encode('utf-8')
    headers = {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(token_url, data=payload, headers=headers)
    with urllib.request.urlopen(req) as response:
        token_data = json.loads(response.read().decode('utf-8'))
    access_token = token_data.get("access_token")
    userinfo_url = f"https://www.googleapis.com/oauth2/v2/userinfo?access_token={access_token}"
    req_user = urllib.request.Request(userinfo_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req_user) as response_user:
        return json.loads(response_user.read().decode('utf-8'))

# ==========================================
# 1. 페이지 설정 & CSS
# ==========================================
st.set_page_config(page_title="ONE - ERP", layout="wide", page_icon="🚢")

if 'lang' not in st.session_state: st.session_state['lang'] = 'KR'

custom_css = """
<style>
    .main .block-container { padding-top: 1.2rem !important; padding-bottom: 1rem !important; }
    
    div[data-testid="stRadio"]:has(input[aria-label="Language"]),
    div[data-testid="stRadio"]:has(input[value="🇰🇷"]) {
        position: fixed !important; top: 10px !important; right: 175px !important;
        z-index: 999999 !important; background: rgba(15, 23, 42, 0.9) !important;
        border: 1px solid #0284C7 !important; padding: 2px 10px !important;
        border-radius: 18px !important; box-shadow: 0 2px 8px rgba(0,0,0,0.3) !important;
    }
    div[data-testid="stRadio"]:has(input[aria-label="Language"]) > div,
    div[data-testid="stRadio"]:has(input[value="🇰🇷"]) > div { flex-direction: row !important; gap: 10px !important; }

    div[data-testid="stFileUploader"] button[data-testid="stBaseButton-icon"],
    div[data-testid="stFileUploader"] button:has(svg[aria-label="Add"]),
    div[data-testid="stFileUploader"] [data-testid="stFileUploaderFile"] + button,
    div[data-testid="stFileUploader"] [data-testid="stFileUploaderFileData"] + button,
    div[data-testid="stFileUploaderDropzone"] + div button { display: none !important; }

    .main-header { background: var(--secondary-background-color); border: 2px solid #0284C7; border-left: 6px solid #0284C7; padding: 16px 20px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
    .main-header h1 { color: var(--text-color); font-size: 1.5rem; font-weight: 800; margin: 0; }
    .main-header p { color: var(--text-color); opacity: 0.85; margin: 4px 0 0 0; font-size: 0.85rem; font-weight: 500; }
    .section-title { color: #0284C7; font-size: 1.05rem; font-weight: 800; margin-bottom: 12px; }
    
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background: var(--secondary-background-color) !important;
        border: 2px solid #0284C7 !important; border-radius: 12px !important;
        padding: 16px !important; margin-bottom: 16px !important; box-shadow: 0 2px 8px rgba(0,0,0,0.15) !important;
    }

    div[data-testid="stExpander"] {
        border: 2px solid #00F0FF !important; border-radius: 12px !important;
        background: linear-gradient(135deg, rgba(0, 240, 255, 0.08) 0%, rgba(29, 78, 216, 0.12) 100%) !important;
        box-shadow: 0 0 15px rgba(0, 240, 255, 0.35) !important; margin-bottom: 20px !important; transition: all 0.3s ease;
    }
    div[data-testid="stExpander"]:hover { box-shadow: 0 0 22px rgba(0, 240, 255, 0.6) !important; border-color: #38BDF8 !important; }
    div[data-testid="stExpander"] summary p { font-size: 1.1rem !important; font-weight: 800 !important; color: #00F0FF !important; text-shadow: 0 0 10px rgba(0, 240, 255, 0.5) !important; }

    div[data-baseweb="select"] div, div[data-baseweb="input"] input { color: #CBD5E1 !important; font-weight: 500 !important; }
    div[data-baseweb="select"] { border-radius: 8px !important; }

    .stButton > button, .google-btn { 
        display: inline-flex !important; align-items: center !important; justify-content: center !important;
        width: 100% !important; background: linear-gradient(135deg, #1D4ED8 0%, #0284C7 100%) !important; 
        color: #FFFFFF !important; font-weight: 700 !important; border: none !important; 
        padding: 8px 16px !important; border-radius: 8px !important; font-size: 0.95rem !important; 
        text-decoration: none !important; box-sizing: border-box !important; height: 42px !important; margin-bottom: 12px !important;
    }
    .google-btn:hover { opacity: 0.9 !important; color: #FFFFFF !important; }
    .loader-container { display: flex; align-items: center; justify-content: center; background: var(--secondary-background-color); border: 2px solid #0284C7; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    .spinner { border: 4px solid rgba(2, 132, 199, 0.2); border-top: 4px solid #0284C7; border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; margin-right: 12px; }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    .loader-text { color: var(--text-color); font-weight: 700; font-size: 1rem; }
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

selected_lang_flag = st.radio("Language", ["🇰🇷", "🇺🇸"], index=0 if st.session_state['lang'] == 'KR' else 1, horizontal=True, label_visibility="collapsed", key="top_lang_radio")
target_lang_code = "KR" if selected_lang_flag == "🇰🇷" else "EN"

if target_lang_code != st.session_state['lang']:
    st.session_state['lang'] = target_lang_code
    st.rerun()

if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
    st.session_state['user_email'] = ""

try: code_param = st.query_params.get("code", None)
except Exception: code_param = None

if code_param and not st.session_state['authenticated']:
    try:
        user_info = get_google_user_info(code_param)
        email = user_info.get("email", "")
        if ALLOWED_DOMAIN and not email.endswith(f"@{ALLOWED_DOMAIN}") and email != "":
            st.error(f"❌ Access Denied: Only @{ALLOWED_DOMAIN} accounts are allowed. (Attempted: {email})")
            st.query_params.clear()
        else:
            st.session_state['authenticated'] = True
            st.session_state['user_email'] = email
            st.query_params.clear()
            st.rerun()
    except urllib.error.HTTPError as http_err:
        st.query_params.clear()
        if http_err.code == 400:
            st.toast("ℹ️ 구글 인증 코드가 만료되었습니다. 다시 로그인 버튼을 눌러주세요.", icon="🔑")
        else:
            st.error(f"Google Auth HTTP Error: {http_err}")
    except Exception:
        st.query_params.clear()
        st.toast("ℹ️ 세션이 재설정되었습니다. 로그인해 주세요.", icon="🔑")

if not st.session_state['authenticated']:
    st.write("")
    st.write("")
    st.write("")
    _, center_col, _ = st.columns([1, 1.5, 1])
    with center_col:
        with st.container(border=True):
            st.markdown("<h2 style='text-align: center; margin-top: 10px; margin-bottom: 4px; font-weight: 800;'>🚢 ONE - ERP</h2>", unsafe_allow_html=True)
            st.markdown(f"<p style='text-align: center; font-size: 0.88rem; color: #94A3B8; margin-bottom: 24px;'>{t('subtitle')}</p>", unsafe_allow_html=True)
            
            auth_url = get_google_auth_url()
            if auth_url:
                st.markdown(f'<a href="{auth_url}" target="_blank" rel="noopener noreferrer" class="google-btn">{t("google_login")}</a>', unsafe_allow_html=True)
            else:
                st.warning("⚠️ GOOGLE_CLIENT_ID 또는 REDIRECT_URI가 설정되지 않았습니다.")
    st.stop()

# 🎯 [로그인 성공 직후 계정별 임시저장(Draft) 자동 복원 실행]
user_email_key = st.session_state.get('user_email', '')
if user_email_key and st.session_state.get('draft_loaded_for_user') != user_email_key:
    user_draft = load_user_draft(user_email_key)
    if user_draft:
        if "doc_info" in user_draft: st.session_state['doc_info'] = user_draft["doc_info"]
        if "doc_items" in user_draft: st.session_state['doc_items'] = pd.DataFrame(user_draft["doc_items"])
        if "visible_cols" in user_draft: st.session_state['visible_cols'] = user_draft["visible_cols"]
        st.toast(f"🎉 '{user_email_key}' 계정의 마지막 작성 내역이 복원되었습니다.", icon="💾")
    st.session_state['draft_loaded_for_user'] = user_email_key

if 'doc_info' not in st.session_state:
    st.session_state['doc_info'] = {"to": "", "attn": "", "project_title": "", "validity": "", "flag_class": "", "our_ref": "", "date": "", "pic": "", "your_ref": "", "ship": "", "payment_due": "", "currency": "", "bottom_remarks": ""}

if 'doc_items' not in st.session_state:
    st.session_state['doc_items'] = pd.DataFrame([{"ItemName": "", "PartNo": "", "Description": "", "Qty": "", "UnitPrice": "", "Amount": "", "Remarks": ""}])

if 'visible_cols' not in st.session_state:
    st.session_state['visible_cols'] = ["ItemName", "PartNo", "Description", "Qty", "UnitPrice", "Amount", "Remarks"]

# ==========================================
# 4. AI 파싱 엔진
# ==========================================
def get_ai_response(api_key, content_list, mode="flash"):
    if not api_key or not str(api_key).strip(): 
        raise Exception("Gemini API Key가 누락되었습니다.")
    genai.configure(api_key=api_key.strip())
    
    primary_model = "gemini-3.6-flash-thinking" if mode == "thinking" else "gemini-3.6-flash"
    candidate_models = [primary_model] if primary_model == "gemini-3.6-flash" else [primary_model, "gemini-3.6-flash"]

    last_err = None
    for model_name in candidate_models:
        for attempt in range(2):
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(content_list)
                if response and response.text:
                    res_text = response.text.strip()
                    res_text = re.sub(r'```(?:json)?', '', res_text).replace('```', '').strip()
                    
                    s_idx = res_text.find('[') if '[' in res_text and (res_text.find('[') < res_text.find('{') or '{' not in res_text) else res_text.find('{')
                    e_idx = res_text.rfind(']') if ']' in res_text and (res_text.rfind(']') > res_text.rfind('}') or '}' not in res_text) else res_text.rfind('}')
                    if s_idx != -1 and e_idx != -1: 
                        res_text = res_text[s_idx:e_idx + 1]
                    return json.loads(res_text)
            except Exception as e:
                last_err = e
                if ("429" in str(e) or "Quota" in str(e)) and attempt == 0:
                    time.sleep(10)
                    continue
                break

    raise Exception(f"Gemini API 요청 실패: {last_err}")

def run_bg_doc_parse(task_state, api_key, file_bytes, file_name, doc_type, ai_mode, sheet_names=None):
    try:
        task_state['status'] = 'running'
        mode_label = "Gemini 3.6 Flash (사고)" if ai_mode == "thinking" else "Gemini 3.6 Flash (고속)"
        task_state['progress_msg'] = f'AI [{mode_label}] 엔진이 문서를 분석 중입니다...'
        
        file_ext = file_name.split('.')[-1].lower()
        save_path = os.path.join(INPUT_DOCS_DIR, f"{get_kst_now().strftime('%Y%m%d_%H%M%S')}_{file_name}")
        with open(save_path, "wb") as f: f.write(file_bytes)

        prompt = """
        Extract document details into JSON format matching the fixed header fields and item list.
        
        CRITICAL RULES FOR EXTRACTION:
        1. ISSUER & RECIPIENT DETAILS:
           - "issuer_company": Name of the company issuing/sending this document.
           - "issuer_pic": Person Name / Contact PIC of the issuing company.
           - "recipient_company": Name of the recipient company.
           - "recipient_attn": Person Name specified in "Attention" / "Attn" of the recipient.

        2. HEADER FIELDS ACCURACY EXTRACTION:
           - "doc_type": The original type of the uploaded document (e.g., "Invoice", "Purchase Order", "Quotation").
           - "date_str": Document Issue Date (e.g. "2026.08.28").
           - "validity": Quotation Validity duration (e.g. "30 Days", "14 Days").
           - "our_ref": Reference Number / Quote No. / PO No. generated by the issuing company.
           - "your_ref": Customer's / Recipient's Reference Number.
           - "to_name", "attn_name", "project_title", "flag_class", "pic", "ship_name", "payment_due", "currency".

        3. ITEM TABLE EXTRACTION:
           - Parse line items into: "ItemName", "PartNo", "Description", "Qty", "UnitPrice", "Amount", "Remarks".

        Return valid JSON EXACTLY matching this structure:
        {
            "doc_type": "", "issuer_company": "", "issuer_pic": "", "recipient_company": "", "recipient_attn": "",
            "to_name": "", "attn_name": "", "project_title": "", "validity": "", "flag_class": "",
            "our_ref": "", "date_str": "", "pic": "", "your_ref": "", "ship_name": "", "payment_due": "", "currency": "",
            "items": [{"ItemName": "", "PartNo": "", "Description": "", "Qty": "", "UnitPrice": "", "Amount": "", "Remarks": ""}]
        }
        """
        if file_ext in ['png', 'jpg', 'jpeg']: content = Image.open(io.BytesIO(file_bytes))
        elif file_ext == 'pdf': content = {"mime_type": "application/pdf", "data": file_bytes}
        elif file_ext in ['xlsx', 'xls']:
            xl = pd.ExcelFile(io.BytesIO(file_bytes))
            sheets_to_parse = sheet_names if sheet_names else xl.sheet_names
            sheets_txt = [f"--- Sheet: {s} ---\n" + pd.read_excel(xl, sheet_name=s).dropna(how='all').dropna(how='all', axis=1).to_csv(index=False) for s in sheets_to_parse]
            content = "Excel Content:\n" + "\n\n".join(sheets_txt)
        elif file_ext == 'csv':
            try: content = "CSV Table Content:\n" + pd.read_csv(io.BytesIO(file_bytes)).dropna(how='all').dropna(how='all', axis=1).to_csv(index=False)
            except Exception: content = "CSV Raw Content:\n" + file_bytes.decode('utf-8', errors='ignore')
        else: content = {"mime_type": "application/pdf", "data": file_bytes}

        ai_data = get_ai_response(api_key, [prompt, content], mode=ai_mode)
        task_state['result'] = {'doc_type': doc_type, 'ai_data': ai_data, 'file_name': file_name}
        task_state['status'] = 'completed'
    except Exception as e:
        task_state['status'] = 'error'
        task_state['error_msg'] = str(e)

def run_bg_doc_ledger_parse(task_state, api_key, file_bytes, file_name, sheet_names, ai_mode):
    try:
        task_state['status'] = 'running'
        mode_label = "Gemini 3.6 Flash (사고)" if ai_mode == "thinking" else "Gemini 3.6 Flash (고속)"
        task_state['progress_msg'] = f'AI [{mode_label}] 엔진이 서류 대장 파일({file_name})을 분석 중입니다...'
        
        file_ext = file_name.split('.')[-1].lower()
        save_path = os.path.join(INPUT_DOCS_DIR, f"{get_kst_now().strftime('%Y%m%d_%H%M%S')}_{file_name}")
        with open(save_path, "wb") as f: f.write(file_bytes)

        all_results = []
        db_prompt = """
        Extract document headers/summaries from the provided file into a JSON Array of objects matching this exact structure:
        [
            {
                "IssueDate": "YYYY-MM-DD",
                "DocDate": "YYYY-MM-DD",
                "DocType": "Quotation",
                "OurRef": "Doc or Title Ref",
                "YourRef": "",
                "ShipName": "",
                "TargetName": "Company Name",
                "Currency": "KRW",
                "TotalAmount": 0.0,
                "ItemCount": 1,
                "CreatedBy": "PIC Name",
                "Status": "🟡 Quoted"
            }
        ]
        CRITICAL RULES:
        - DO NOT STOP AT BLANK ROWS in spreadsheets/documents. Scan completely to bottom.
        - If multiple documents/sections exist in one file, create separate header entries.
        """

        if file_ext in ['png', 'jpg', 'jpeg', 'pdf']:
            content = Image.open(io.BytesIO(file_bytes)) if file_ext in ['png', 'jpg', 'jpeg'] else {"mime_type": "application/pdf", "data": file_bytes}
            res = get_ai_response(api_key, [db_prompt, content], mode=ai_mode)
            extracted = extract_items_list(res)
            if extracted: all_results.extend(extracted)
        elif file_ext in ['xlsx', 'xls']:
            excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
            sheets_to_parse = sheet_names if sheet_names else excel_file.sheet_names
            for idx, s_name in enumerate(sheets_to_parse):
                task_state['progress_msg'] = f"[{idx+1}/{len(sheets_to_parse)}] '{s_name}' 시트 추출 중..."
                try:
                    df_clean = pd.read_excel(excel_file, sheet_name=s_name).dropna(how='all')
                    if not df_clean.empty:
                        res = get_ai_response(api_key, [db_prompt, f"Sheet '{s_name}' CSV Content:\n{df_clean.to_csv(index=False)}"], mode=ai_mode)
                        extracted = extract_items_list(res)
                        if extracted: all_results.extend(extracted)
                except Exception: pass
        elif file_ext == 'csv':
            try:
                df_clean = pd.read_csv(io.BytesIO(file_bytes)).dropna(how='all')
                if not df_clean.empty:
                    res = get_ai_response(api_key, [db_prompt, f"CSV Content:\n{df_clean.to_csv(index=False)}"], mode=ai_mode)
                    extracted = extract_items_list(res)
                    if extracted: all_results.extend(extracted)
            except Exception: pass

        if not all_results:
            task_state['status'] = 'error'
            task_state['error_msg'] = "파싱된 서류 데이터가 없습니다. 업로드된 문서의 내용이나 API 키를 확인해 주세요."
            return

        parsed_df = ensure_cols(pd.DataFrame(all_results), doc_db_cols)
        task_state['result'] = clean_df(parsed_df)
        task_state['status'] = 'completed'
    except Exception as e:
        task_state['status'] = 'error'
        task_state['error_msg'] = str(e)

def run_bg_item_master_parse(task_state, api_key, file_bytes, file_name, sheet_names, ai_mode):
    try:
        task_state['status'] = 'running'
        mode_label = "Gemini 3.6 Flash (사고)" if ai_mode == "thinking" else "Gemini 3.6 Flash (고속)"
        task_state['progress_msg'] = f'AI [{mode_label}] 엔진이 자재 단가표({file_name})를 파싱 중입니다...'
        
        file_ext = file_name.split('.')[-1].lower()
        save_path = os.path.join(INPUT_DOCS_DIR, f"{get_kst_now().strftime('%Y%m%d_%H%M%S')}_{file_name}")
        with open(save_path, "wb") as f: f.write(file_bytes)

        all_results = []
        item_prompt = """
        Extract ALL individual material/part price items from the provided file into a JSON Array.

        CRITICAL PARSING RULES:
        1. DO NOT STOP AT BLANK ROWS: Scan top to bottom completely. Parse every valid item row.
        2. Extract ItemName, PartNo, Description, Supplier, BuyPrice, ListPrice, Currency, Remarks.
        3. If BuyPrice or ListPrice has text like "(부가세 별도)" or notes, extract numerical price into BuyPrice/ListPrice and put notes into Remarks.

        Expected JSON Array Format:
        [
            {
                "ItemName": "",
                "PartNo": "",
                "Description": "",
                "Supplier": "공급사명 (예: (주)더주원)",
                "BuyPrice": 0.0,
                "ListPrice": 0.0,
                "Currency": "KRW",
                "Remarks": ""
            }
        ]
        """

        if file_ext in ['png', 'jpg', 'jpeg', 'pdf']:
            content = Image.open(io.BytesIO(file_bytes)) if file_ext in ['png', 'jpg', 'jpeg'] else {"mime_type": "application/pdf", "data": file_bytes}
            res = get_ai_response(api_key, [item_prompt, content], mode=ai_mode)
            extracted = extract_items_list(res)
            if extracted: all_results.extend(extracted)
        elif file_ext in ['xlsx', 'xls']:
            excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
            sheets_to_parse = sheet_names if sheet_names else excel_file.sheet_names
            for idx, s_name in enumerate(sheets_to_parse):
                task_state['progress_msg'] = f"[{idx+1}/{len(sheets_to_parse)}] '{s_name}' 시트 자재 파싱 중..."
                try:
                    df_clean = pd.read_excel(excel_file, sheet_name=s_name).dropna(how='all')
                    if not df_clean.empty:
                        res = get_ai_response(api_key, [item_prompt, f"Excel Content (Sheet: {s_name}):\n{df_clean.to_csv(index=False)}"], mode=ai_mode)
                        extracted = extract_items_list(res)
                        if extracted: all_results.extend(extracted)
                except Exception: pass
        elif file_ext == 'csv':
            try:
                df_clean = pd.read_csv(io.BytesIO(file_bytes)).dropna(how='all')
                if not df_clean.empty:
                    res = get_ai_response(api_key, [item_prompt, f"CSV Content:\n{df_clean.to_csv(index=False)}"], mode=ai_mode)
                    extracted = extract_items_list(res)
                    if extracted: all_results.extend(extracted)
            except Exception: pass

        if not all_results:
            task_state['status'] = 'error'
            task_state['error_msg'] = "파싱된 자재 데이터가 없습니다. AI 응답을 확인해 주세요."
            return

        parsed_df = ensure_cols(pd.DataFrame(all_results), item_master_cols)
        task_state['result'] = clean_df(parsed_df)
        task_state['status'] = 'completed'
    except Exception as e:
        task_state['status'] = 'error'
        task_state['error_msg'] = str(e)

def start_bg_thread(target_func, args):
    t = threading.Thread(target=target_func, args=args)
    add_script_run_ctx(t)
    t.start()

def generate_pdf(context):
    if not HAS_WEASYPRINT:
        raise RuntimeError("PDF 생성 엔진(WeasyPrint)이 시스템에 설치되어 있지 않습니다.")
    logo_path = os.path.abspath("logo.png")
    context["logo_base64"] = base64.b64encode(open(logo_path, "rb").read()).decode('utf-8') if os.path.exists(logo_path) else None
    
    env = Environment()
    template = env.from_string(INLINE_HTML_TEMPLATE)
    return HTML(string=template.render(context)).write_pdf()

def render_pdf_images(pdf_bytes):
    images = []
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            images.append(pix.tobytes("png"))
    except Exception: pass
    return images

# ==========================================
# 5. UI 및 사이드바 Navigation (4대 메뉴)
# ==========================================
st.sidebar.title("🚢 ONE - ERP")
if st.session_state.get('user_email'):
    st.sidebar.markdown(f"{t('user_label')} `{st.session_state['user_email']}`")
    if st.sidebar.button(t("logout")):
        st.session_state['authenticated'] = False
        st.session_state['user_email'] = ""
        st.session_state['draft_loaded_for_user'] = None
        st.rerun()

st.sidebar.markdown("""
<div style="
    background: rgba(0, 240, 255, 0.1);
    border: 1.5px solid #00F0FF;
    border-radius: 8px;
    padding: 10px 12px;
    text-align: center;
    margin-bottom: 12px;
    box-shadow: 0 0 12px rgba(0, 240, 255, 0.35);
">
    <span style="
        color: #00F0FF;
        font-size: 0.85rem;
        font-weight: 800;
        text-shadow: 0 0 8px rgba(0, 240, 255, 0.6);
        letter-spacing: 0.3px;
    ">Powered by Gemini 3.6</span>
</div>
""", unsafe_allow_html=True)

live_rates, rate_time = get_exchange_rates()
usd_krw = live_rates.get("KRW", 1350.0)
eur_usd = live_rates.get("EUR", 0.92)
eur_krw = usd_krw / eur_usd if eur_usd else 1480.0
sgd_usd = live_rates.get("SGD", 1.35)
sgd_krw = usd_krw / sgd_usd if sgd_usd else 1000.0

st.sidebar.markdown(f"""
<div style="background: rgba(2, 132, 199, 0.1); border: 1px solid #0284C7; border-radius: 8px; padding: 10px 12px; margin-bottom: 16px;">
    <div style="display: flex; justify-content: space-between; align-items: center; padding: 3px 0; border-bottom: 1px dashed rgba(2, 132, 199, 0.25);">
        <span style="font-size: 0.78rem; color: #94A3B8; font-weight: 600;">🇺🇸 USD / KRW</span>
        <span style="font-size: 0.82rem; color: #F1F5F9; font-weight: 700; font-family: monospace;">{usd_krw:,.2f} 원</span>
    </div>
    <div style="display: flex; justify-content: space-between; align-items: center; padding: 3px 0; border-bottom: 1px dashed rgba(2, 132, 199, 0.25);">
        <span style="font-size: 0.78rem; color: #94A3B8; font-weight: 600;">🇪🇺 EUR / KRW</span>
        <span style="font-size: 0.82rem; color: #F1F5F9; font-weight: 700; font-family: monospace;">{eur_krw:,.2f} 원</span>
    </div>
    <div style="display: flex; justify-content: space-between; align-items: center; padding: 3px 0;">
        <span style="font-size: 0.78rem; color: #94A3B8; font-weight: 600;">🇸🇬 SGD / KRW</span>
        <span style="font-size: 0.82rem; color: #F1F5F9; font-weight: 700; font-family: monospace;">{sgd_krw:,.2f} 원</span>
    </div>
</div>
""", unsafe_allow_html=True)

menu_options = [t("menu_gen"), t("menu_db_master"), t("menu_history"), t("menu_admin")]
menu_selection = st.sidebar.radio(t("sys_menu"), menu_options)

if menu_selection == t("menu_gen"): menu = "서류 파이프라인 Master"
elif menu_selection == t("menu_db_master"): menu = "통합 DB 마스터"
elif menu_selection == t("menu_admin"): menu = "관리자 메뉴"
else: menu = "서류 이력 & 갤러리"

if 'current_menu' not in st.session_state: st.session_state['current_menu'] = menu
elif st.session_state['current_menu'] != menu:
    st.session_state['current_menu'] = menu
    if st.session_state['bg_task']['status'] == 'error':
        st.session_state['bg_task'] = {'status': 'idle', 'type': None, 'progress_msg': '', 'result': None, 'error_msg': None}

task = st.session_state['bg_task']
if is_running:
    st.markdown(f"""<div class="loader-container"><div class="spinner"></div><div class="loader-text">{task['progress_msg']} <br><span style='font-size:0.85rem; color:var(--text-color); opacity:0.75; font-weight:500;'>작업 중에도 다른 메뉴로 자유롭게 이동하실 수 있습니다.</span></div></div>""", unsafe_allow_html=True)

if task['status'] == 'error':
    st.error(f"❌ AI 분석 작업 중 오류가 발생했습니다: {task['error_msg']}")

# ==========================================
# 6. 서류 파이프라인 Master
# ==========================================
if menu == "서류 파이프라인 Master":
    pipeline_dir = st.sidebar.radio("🔄 업무 방향 선택 (Direction)", ["🟢 매출 업무 (Sales / Outbound)", "🔵 매입 업무 (Procurement / Inbound)"])
    is_outbound = "매출" in pipeline_dir
    
    doc_type_opts = ["Quotation", "Purchase Order", "Invoice", "Delivery Note", "Service Report", "Credit Note"] if is_outbound else ["Inquiry / RFQ", "Supplier Quote", "Purchase Order", "Supplier Invoice"]
    doc_type = st.sidebar.selectbox("📋 서류 유형 선택", doc_type_opts)
    
    st.markdown(f"""<div class="main-header"><h1>{t('doc_gen_title')} - {pipeline_dir.split()[1]} ({doc_type})</h1><p>{t('doc_gen_desc')}</p></div>""", unsafe_allow_html=True)

    # 🎯 [시각적 자동 임시저장 상태 안내 바]
    curr_user_mail = st.session_state.get('user_email', 'Unknown')
    st.caption(f"💾 **실시간 자동 임시저장 활성화** (계정: `{curr_user_mail}` | 페이지 재접속/로그인 시 100% 자동으로 이전 작성 내역 복원)")

    our_ledger, cust_ledger, history = safe_read_csv(OUR_DB_FILE, doc_db_cols), safe_read_csv(CUSTOMER_DB_FILE, doc_db_cols), load_history()
    partner_df = safe_read_csv(PARTNER_DB_FILE, partner_db_cols)
    vessel_df = safe_read_csv(VESSEL_DB_FILE, vessel_db_cols)

    db_to_options = sorted(list(set([str(x).strip() for x in (partner_df["PartnerName"].tolist() + our_ledger["TargetName"].tolist() + cust_ledger["TargetName"].tolist() + history.get("to_list", [])) if str(x).strip() and str(x).strip() not in ["-", "nan", "None"]])))
    db_ship_options = sorted(list(set([str(x).strip() for x in (vessel_df["ShipName"].tolist() + our_ledger["ShipName"].tolist() + cust_ledger["ShipName"].tolist() + history.get("ships", [])) if str(x).strip() and str(x).strip() not in ["-", "nan", "None"]])))
    db_attn_options = sorted(list(set([str(x).strip() for x in (partner_df["PIC"].tolist() + history.get("attns", [])) if str(x).strip() and str(x).strip() not in ["-", "nan", "None"]])))
    db_our_ref_options = sorted(list(set([str(x).strip() for x in our_ledger["OurRef"].tolist() if str(x).strip() and str(x).strip() not in ["-", "nan", "None"]])))
    db_your_ref_options = sorted(list(set([str(x).strip() for x in (our_ledger["YourRef"].tolist() + cust_ledger["YourRef"].tolist()) if str(x).strip() and str(x).strip() not in ["-", "nan", "None"]])))

    with st.container(border=True):
        st.markdown("⚡ **[1초 서류 승계] 이전 서류 불러와서 자동 작성**")
        all_ref_options = ["-- 이전 서류 Ref No. 선택하여 헤더 및 품목 100% 불러오기 --"] + db_our_ref_options + db_your_ref_options
        selected_ref_carry = st.selectbox("불러올 서류 Ref 번호", options=all_ref_options, key="carry_over_ref_select")
        if selected_ref_carry and selected_ref_carry != all_ref_options[0]:
            matched_row = our_ledger[our_ledger["OurRef"] == selected_ref_carry]
            if matched_row.empty: matched_row = cust_ledger[cust_ledger["YourRef"] == selected_ref_carry]
            if not matched_row.empty:
                r = matched_row.iloc[0]
                st.session_state['doc_info'].update({
                    "to": r.get("TargetName", ""), "ship": r.get("ShipName", ""),
                    "our_ref": r.get("OurRef", ""), "your_ref": r.get("YourRef", ""),
                    "currency": r.get("Currency", "KRW")
                })
                st.session_state['to_txt'] = r.get("TargetName", "")
                st.session_state['ship_txt'] = r.get("ShipName", "")
                st.session_state['our_ref_txt'] = r.get("OurRef", "")
                st.session_state['your_ref_txt'] = r.get("YourRef", "")
                st.session_state['currency_txt'] = r.get("Currency", "KRW")
                st.toast(f"✅ 서류 '{selected_ref_carry}' 정보 승계 완료", icon="🚀")

    if task['status'] == 'completed' and task['type'] == 'doc_parse':
        ai_data = task['result']['ai_data']
        file_name = task['result'].get('file_name', '')
        
        issuer_comp = clean_str(ai_data.get("issuer_company", ""))
        issuer_pic = clean_str(ai_data.get("issuer_pic", "")) or clean_str(ai_data.get("pic", ""))
        recip_comp = clean_str(ai_data.get("recipient_company", "")) or clean_str(ai_data.get("to_name", ""))
        recip_attn = clean_str(ai_data.get("recipient_attn", "")) or clean_str(ai_data.get("attn_name", ""))

        issuer_check = (issuer_comp + " " + issuer_pic).lower()
        is_our_company_issuer = any(kw in issuer_check for kw in ["1solution", "원솔루션", "one solution"]) or (ALLOWED_DOMAIN in issuer_check)
        current_logged_user = st.session_state.get('user_email', '').split('@')[0].upper() if st.session_state.get('user_email') else "ONE SOLUTION"

        if not is_our_company_issuer and issuer_comp:
            to_field_val, attn_field_val = issuer_comp, issuer_pic
            your_ref_val = clean_str(ai_data.get("our_ref", "")) or clean_str(ai_data.get("your_ref", ""))
            our_ref_val = ""
            pic_field_val = recip_attn if recip_attn else current_logged_user
        else:
            to_field_val, attn_field_val = recip_comp, recip_attn
            your_ref_val = clean_str(ai_data.get("your_ref", ""))
            our_ref_val = clean_str(ai_data.get("our_ref", ""))
            pic_field_val = issuer_pic if issuer_pic else current_logged_user

        project_val = clean_str(ai_data.get("project_title", ""))
        validity_val = clean_str(ai_data.get("validity", "30 Days"))
        flag_class_val = clean_str(ai_data.get("flag_class", ""))
        date_val = clean_str(ai_data.get("date_str", get_kst_now().strftime("%Y-%m-%d")))
        ship_val = clean_str(ai_data.get("ship_name", ""))
        payment_due_val = clean_str(ai_data.get("payment_due", ""))
        currency_val = clean_str(ai_data.get("currency", "KRW"))

        parsed_items = ai_data.get("items", [])
        parsed_tot_val = sum([safe_float(p.get("Amount", 0)) or (safe_float(p.get("Qty", 0)) * safe_float(p.get("UnitPrice", 0))) for p in parsed_items])

        st.session_state['parsed_input_doc'] = {
            "DocType": clean_str(ai_data.get("doc_type", doc_type)) or "Invoice",
            "YourRef": your_ref_val or clean_str(ai_data.get("our_ref", "")) or file_name,
            "OurRef": "-", "ShipName": ship_val or "-",
            "TargetName": issuer_comp or to_field_val or recip_comp or "-",
            "DocDate": date_val or get_kst_now().strftime("%Y-%m-%d"),
            "Currency": currency_val or "KRW",
            "TotalAmount": parsed_tot_val,
            "ItemCount": len(parsed_items) if parsed_items else 1,
            "CreatedBy": issuer_pic or "External Supplier/Customer"
        }

        st.session_state['doc_info'] = {
            "to": to_field_val, "attn": attn_field_val, "project_title": project_val,
            "validity": validity_val, "flag_class": flag_class_val, "our_ref": our_ref_val,
            "date": date_val, "pic": pic_field_val, "your_ref": your_ref_val, "ship": ship_val, 
            "payment_due": payment_due_val, "currency": currency_val, 
            "bottom_remarks": st.session_state['doc_info'].get("bottom_remarks", "")
        }

        st.session_state['to_txt'] = to_field_val
        st.session_state['attn_txt'] = attn_field_val
        st.session_state['project_title_txt'] = project_val
        st.session_state['our_ref_txt'] = our_ref_val
        st.session_state['your_ref_txt'] = your_ref_val
        st.session_state['date_date_str'] = date_val
        st.session_state['validity_txt'] = validity_val
        st.session_state['payment_due_txt'] = payment_due_val
        st.session_state['pic_txt'] = pic_field_val
        st.session_state['ship_txt'] = ship_val

        if "/" in flag_class_val:
            fc_parts = flag_class_val.split("/", 1)
            st.session_state['flag_txt'] = fc_parts[0].strip()
            st.session_state['class_txt'] = fc_parts[1].strip()
        else:
            st.session_state['flag_txt'] = flag_class_val
            st.session_state['class_txt'] = ''

        st.session_state['currency_txt'] = currency_val
        st.session_state['last_currency'] = currency_val
        
        items_df = pd.DataFrame(parsed_items) if parsed_items else pd.DataFrame()
        if not items_df.empty:
            st.session_state['doc_items'] = recalculate_items_df(items_df, currency=currency_val)
            
        st.session_state['bg_task']['status'] = 'idle'
        save_user_draft(st.session_state.get('user_email', ''), st.session_state['doc_info'], st.session_state['doc_items'], st.session_state.get('visible_cols'))
        st.success("✅ AI 문서 분석 완료 & 타사 인풋/자사 아웃풋 역할 자동 분리.")

    left_col, right_col = st.columns([5, 5])

    with left_col:
        with st.expander(t("ai_expander_title"), expanded=False):
            ai_mode_choice = st.radio(t("ai_mode_label"), [t("mode_flash"), t("mode_thinking")], horizontal=True, disabled=is_running)
            selected_mode = "thinking" if "Thinking" in ai_mode_choice or "사고" in ai_mode_choice else "flash"
            uploaded_doc = st.file_uploader(t("upload_doc_label"), type=["pdf", "png", "jpg", "jpeg", "xlsx", "xls", "csv"], accept_multiple_files=False, disabled=is_running)
            
            if uploaded_doc:
                up_ext = uploaded_doc.name.split('.')[-1].lower()
                if up_ext in ['xlsx', 'xls']:
                    try:
                        excel_obj = pd.ExcelFile(io.BytesIO(uploaded_doc.getvalue()))
                        sheet_names = excel_obj.sheet_names
                        parse_mode = st.radio("파싱 모드", ["📌 특정 시트 선택", "🚀 전체 시트 파싱"], horizontal=True, disabled=is_running, key="doc_gen_parse_mode")
                        if parse_mode == "📌 특정 시트 선택":
                            selected_sheet = st.selectbox("시트 선택", sheet_names, disabled=is_running, key="doc_gen_sheet_sel")
                            if st.button(t("btn_ai_parse"), disabled=is_running, key="btn_doc_gen_analyze_sheet"):
                                st.session_state['bg_task']['type'] = 'doc_parse'
                                start_bg_thread(run_bg_doc_parse, (st.session_state['bg_task'], gemini_key, uploaded_doc.getvalue(), uploaded_doc.name, doc_type, selected_mode, [selected_sheet]))
                                st.rerun()
                        else:
                            if st.button("🚀 전체 파싱", disabled=is_running, key="btn_doc_gen_parse_all"):
                                st.session_state['bg_task']['type'] = 'doc_parse'
                                start_bg_thread(run_bg_doc_parse, (st.session_state['bg_task'], gemini_key, uploaded_doc.getvalue(), uploaded_doc.name, doc_type, selected_mode, sheet_names))
                                st.rerun()
                    except Exception as e: st.error(f"❌ 엑셀 로딩 오류: {e}")
                else:
                    if st.button(t("btn_ai_parse"), disabled=is_running, key="btn_doc_gen_analyze_direct"):
                        st.session_state['bg_task']['type'] = 'doc_parse'
                        start_bg_thread(run_bg_doc_parse, (st.session_state['bg_task'], gemini_key, uploaded_doc.getvalue(), uploaded_doc.name, doc_type, selected_mode, []))
                        st.rerun()

        if st.button(t("btn_reset"), disabled=is_running):
            st.session_state['doc_info'] = {"to": "", "attn": "", "project_title": "", "validity": "", "flag_class": "", "our_ref": "", "date": "", "pic": "", "your_ref": "", "ship": "", "payment_due": "", "currency": "", "bottom_remarks": ""}
            st.session_state['doc_items'] = pd.DataFrame([{"ItemName": "", "PartNo": "", "Description": "", "Qty": "", "UnitPrice": "", "Amount": "", "Remarks": ""}])
            st.session_state['last_currency'] = "KRW"
            if 'parsed_input_doc' in st.session_state: del st.session_state['parsed_input_doc']
            save_user_draft(st.session_state.get('user_email', ''), st.session_state['doc_info'], st.session_state['doc_items'], st.session_state.get('visible_cols'))
            st.rerun()

        with st.container(border=True):
            st.markdown(f'<div class="section-title">{t("hdr_title", doc_type=doc_type)}</div>', unsafe_allow_html=True)
            col_hdr_l, col_hdr_r = st.columns(2)
            
            with col_hdr_l:
                st.markdown("**[상대방 정보 / Target]**")
                to_name = render_unified_input("To (거래처)", st.session_state['doc_info'].get("to", ""), db_to_options, "to")
                attn_name = render_unified_input("Attention (담당자)", st.session_state['doc_info'].get("attn", ""), db_attn_options, "attn")
                your_ref = render_unified_input("Your Ref. No.", st.session_state['doc_info'].get("your_ref", ""), db_your_ref_options, "your_ref")
                ship_name = render_unified_input("Ship's Name (선박명)", st.session_state['doc_info'].get("ship", ""), db_ship_options, "ship")

                curr_fc = clean_str(st.session_state['doc_info'].get("flag_class", ""))
                fc_parts = curr_fc.split("/", 1) if "/" in curr_fc else [curr_fc, ""]
                col_fc1, col_fc2 = st.columns(2)
                with col_fc1: sel_flag = render_unified_input("Flag", fc_parts[0].strip(), FLAG_OPTIONS, "flag")
                with col_fc2: sel_class = render_unified_input("Class", fc_parts[1].strip(), CLASS_OPTIONS, "class")
                flag_class = f"{sel_flag} / {sel_class}".strip(" /")

            with col_hdr_r:
                st.markdown("**[발신자 정보 / Issuer]**")
                pic_name = render_unified_input("PIC (작성자)", st.session_state['doc_info'].get("pic", ""), [st.session_state.get('user_email', '')], "pic")
                date_str = render_date_input("Date (발행일)", st.session_state['doc_info'].get("date", ""), "date")
                our_ref = render_unified_input("Our Ref. No.", st.session_state['doc_info'].get("our_ref", ""), db_our_ref_options, "our_ref")
                validity = render_unified_input("Validity", st.session_state['doc_info'].get("validity", ""), ["30 Days", "14 Days", "60 Days"], "validity")
                payment_due = render_unified_input("Payment Due", st.session_state['doc_info'].get("payment_due", ""), ["30 Days Net", "Immediate", "50% Advance / 50% Balance"], "payment_due")

            project_title = render_unified_input("Project Title", st.session_state['doc_info'].get("project_title", ""), [], "project_title")
            currency = render_unified_input("Currency (통화)", st.session_state['doc_info'].get("currency", "KRW"), CURRENCY_OPTIONS, "currency")
            curr_currency = currency if currency else "KRW"
            curr_sym = get_currency_symbol(curr_currency)

            st.session_state['doc_info'].update({
                "to": to_name, "attn": attn_name, "your_ref": your_ref, "ship": ship_name,
                "flag_class": flag_class, "pic": pic_name, "date": date_str, "our_ref": our_ref,
                "validity": validity, "payment_due": payment_due, "project_title": project_title, "currency": curr_currency
            })

            if 'last_currency' not in st.session_state: st.session_state['last_currency'] = curr_currency
            last_curr = st.session_state['last_currency']
            if last_curr != curr_currency and last_curr and curr_currency:
                rate_old = live_rates.get(last_curr, 1.0)
                rate_new = live_rates.get(curr_currency, 1.0)
                if rate_old > 0 and rate_new > 0 and rate_old != rate_new:
                    factor = rate_new / rate_old
                    df_conv = st.session_state['doc_items'].copy()
                    for idx in df_conv.index:
                        u_p, amt = safe_float(df_conv.at[idx, 'UnitPrice']), safe_float(df_conv.at[idx, 'Amount'])
                        if u_p > 0: df_conv.at[idx, 'UnitPrice'] = f"{(u_p * factor):,.0f}" if curr_currency in ["KRW", "JPY"] else f"{(u_p * factor):,.2f}"
                        if amt > 0: df_conv.at[idx, 'Amount'] = f"{(amt * factor):,.0f}" if curr_currency in ["KRW", "JPY"] else f"{(amt * factor):,.2f}"
                    st.session_state['doc_items'] = df_conv
                    st.session_state['doc_info']['currency'] = curr_currency
                    st.toast(f"💱 실시간 환율 자동 단가 변환 ({last_curr} → {curr_currency})", icon="ℹ️")
                st.session_state['last_currency'] = curr_currency

            st.markdown(f'<div class="section-title" style="margin-top:20px;">{t("items_title")}</div>', unsafe_allow_html=True)
            
            all_possible_cols = ["ItemName", "PartNo", "Description", "Qty", "UnitPrice", "Amount", "Remarks"]
            visible_cols_selected = st.multiselect(
                "👁️ 표에 표시할 열 선택 (계정별 숨김 설정 저장됨)",
                options=all_possible_cols,
                default=st.session_state.get('visible_cols', all_possible_cols),
                key="visible_cols_selector"
            )
            st.session_state['visible_cols'] = visible_cols_selected

            item_master_data = clean_df(ensure_cols(safe_read_csv(ITEM_MASTER_FILE, item_master_cols), item_master_cols))
            if not item_master_data.empty:
                item_options = ["-- 자재 단가 마스터 DB에서 품목 선택하여 자동 입력 --"] + [
                    f"[{row['ItemName']}] {row['PartNo']} | ListPrice: {row['ListPrice']} {row['Currency']} ({row['Supplier']})"
                    for _, row in item_master_data.iterrows()
                ]
                selected_master_item = st.selectbox("🔍 자재 단가 마스터 DB 품목 불러오기", options=item_options, key="quick_load_item_master")
                if selected_master_item and selected_master_item != item_options[0]:
                    selected_idx = item_options.index(selected_master_item) - 1
                    target_item_row = item_master_data.iloc[selected_idx]
                    
                    new_item_row = {
                        "ItemName": target_item_row.get("ItemName", ""),
                        "PartNo": target_item_row.get("PartNo", ""),
                        "Description": target_item_row.get("Description", ""),
                        "Qty": "1",
                        "UnitPrice": str(target_item_row.get("ListPrice", "")),
                        "Amount": str(target_item_row.get("ListPrice", "")),
                        "Remarks": target_item_row.get("Remarks", "")
                    }
                    curr_items = clean_df(st.session_state['doc_items'].copy())
                    updated_items = pd.concat([curr_items, pd.DataFrame([new_item_row])], ignore_index=True)
                    st.session_state['doc_items'] = recalculate_items_df(updated_items, currency=curr_currency)
                    save_user_draft(st.session_state.get('user_email', ''), st.session_state['doc_info'], st.session_state['doc_items'], st.session_state['visible_cols'])
                    st.success(f"✅ '{target_item_row.get('ItemName')}' 품목이 입력 표에 추가되었습니다.")
                    st.rerun()

            raw_df = clean_df(st.session_state['doc_items'].copy())
            recalced_df = recalculate_items_df(raw_df, currency=curr_currency)
            
            recalced_df.insert(0, "No.", range(1, len(recalced_df) + 1))
            display_cols = ["No."] + [c for c in visible_cols_selected if c in recalced_df.columns]
            df_for_editor = recalced_df[display_cols]

            edited_df = st.data_editor(
                df_for_editor, 
                num_rows="dynamic", 
                use_container_width=True, 
                key="main_doc_items_editor",
                column_config={"No.": st.column_config.NumberColumn("No.", disabled=True, width="small")}
            )

            save_edited_df = edited_df.drop(columns=["No."], errors="ignore")
            st.session_state['doc_items'] = recalculate_items_df(save_edited_df, currency=curr_currency)

            save_user_draft(st.session_state.get('user_email', ''), st.session_state['doc_info'], st.session_state['doc_items'], st.session_state['visible_cols'])

            calc_total_val = st.session_state['doc_items']["Amount"].apply(safe_float).sum()
            fmt_tot = f"{calc_total_val:,.0f}" if curr_currency in ["KRW", "JPY"] else f"{calc_total_val:,.2f}"
            default_total_str = f"{curr_sym}{fmt_tot}"

            col_tot1, col_tot2 = st.columns([1, 1])
            with col_tot1: custom_total_input = st.text_input("Total Amount", value=default_total_str, key="custom_total_input")
            with col_tot2: vat_note_input = st.text_input("VAT 하단 안내", value="(Excl. VAT 10%)", key="vat_note_input")

            final_total_str = custom_total_input.strip() or default_total_str
            vat_note_str = vat_note_input.strip()

            st.markdown(f'<div class="section-title" style="margin-top:20px;">{t("remarks_title")}</div>', unsafe_allow_html=True)
            bottom_remarks = st.text_area("Remarks", value=st.session_state['doc_info'].get("bottom_remarks", ""), height=80, key="txt_bottom_remarks")
            st.session_state['doc_info']["bottom_remarks"] = bottom_remarks

            st.markdown(f'<div class="section-title" style="margin-top:20px;">{t("reg_title")}</div>', unsafe_allow_html=True)
            reg_pwd = st.text_input(t("pwd_save_label"), type="password", key="doc_reg_pwd")

            btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 1])
            with btn_col1:
                if st.button("🚀 스마트 일괄 자동 등록 ", key="btn_reg_all_batch", disabled=is_running):
                    if reg_pwd != SAVE_PASSWORD: st.error(t("pwd_err"))
                    else:
                        target_db = OUR_DB_FILE if is_outbound else CUSTOMER_DB_FILE
                        save_to_doc_ledger(target_db, doc_type, your_ref, our_ref, ship_name, to_name, date_str, currency, calc_total_val, len(st.session_state['doc_items']), st.session_state.get('user_email'))
                        save_history(ship_name, to_name, attn_name)

                        count = save_items_to_master(st.session_state['doc_items'], supplier_name=to_name or "자사 서류 생성", currency=curr_currency)
                        st.success(f"🎉 스마트 일괄 등록 완료!\n- 서류 헤더 → 서류 대장 저장\n- 자재/품목 {count}건 → 자재 마스터 DB 저장")

            with btn_col2:
                if st.button("🏢 서류 대장에 서류 등록", key="btn_reg_header_only", disabled=is_running):
                    if reg_pwd != SAVE_PASSWORD: st.error(t("pwd_err"))
                    else:
                        target_db = OUR_DB_FILE if is_outbound else CUSTOMER_DB_FILE
                        save_to_doc_ledger(target_db, doc_type, your_ref, our_ref, ship_name, to_name, date_str, currency, calc_total_val, len(st.session_state['doc_items']), st.session_state.get('user_email'))
                        save_history(ship_name, to_name, attn_name)
                        st.success("🎉 서류 대장에 헤더 정보가 등록되었습니다.")
            
            with btn_col3:
                if st.button("📦 자재 마스터 DB에 품목 등록", key="btn_reg_items_only", disabled=is_running):
                    if reg_pwd != SAVE_PASSWORD: st.error(t("pwd_err"))
                    else:
                        count = save_items_to_master(st.session_state['doc_items'], supplier_name=to_name or "자사 서류 생성", currency=curr_currency)
                        st.success(f"🎉 자재 단가 마스터 DB에 총 {count}개 품목이 등록되었습니다.")

    with right_col:
        with st.container(border=True):
            st.markdown(f'<div class="section-title">{t("preview_title")}</div>', unsafe_allow_html=True)
            preview_ctx = {
                "doc_title": doc_type.upper(), "to_name": to_name, "attn_name": attn_name, "project_title": project_title,
                "validity": validity, "flag_class": flag_class, "our_ref": our_ref, "date_str": date_str or get_kst_now().strftime("%Y-%m-%d"),
                "pic": pic_name, "your_ref": your_ref, "ship_name": ship_name, "payment_due": payment_due, "currency": currency or "KRW",
                "items": prepare_items_for_pdf(clean_df(st.session_state['doc_items']).to_dict("records"), currency=curr_currency),
                "bottom_remarks": bottom_remarks, "total_amount_str": final_total_str, "vat_note": vat_note_str
            }
            try:
                realtime_pdf_bytes = generate_pdf(preview_ctx)
                
                # 🎯 [매입 서류 미리보기 파일명 특수문자 정제 - Errno 2 완전 차단]
                safe_doc_filename = re.sub(r'[\\/*?:"<>|]', '_', clean_str(doc_type))
                file_n = f"{safe_doc_filename}_{our_ref or your_ref or 'Draft'}.pdf"
                
                with open(os.path.join("output", file_n), "wb") as f: f.write(realtime_pdf_bytes)
                st.download_button(t("btn_download_pdf"), realtime_pdf_bytes, file_name=file_n, mime="application/pdf", key="rt_download")
                
                pdf_imgs = render_pdf_images(realtime_pdf_bytes)
                if pdf_imgs:
                    for i, img_b in enumerate(pdf_imgs): st.image(img_b, caption=f"Page {i+1}", use_container_width=True)
            except Exception as pdf_err:
                st.warning(f"⚠️ PDF 미리보기 생성 대기 중: {pdf_err}")

# ==========================================
# 7. 📦 통합 DB 마스터
# ==========================================
elif menu == "통합 DB 마스터":
    st.markdown("""<div class="main-header"><h1>📦 통합 DB 마스터 (Unified Master Database)</h1><p>자재 단가, 거래처, 선박 정보 및 서류 관리 대장을 한 화면에서 선택 관리합니다.</p></div>""", unsafe_allow_html=True)

    db_choice = st.selectbox(
        "📂 관리할 데이터베이스(DB) 선택",
        ["📦 자재/품목 마스터 DB", "🤝 통합 거래처 DB (Partners)", "🚢 선박/프로젝트 DB (Vessels)", "📊 서류 통합 관리 대장 (Document Ledgers)"]
    )

    if db_choice == "📦 자재/품목 마스터 DB":
        item_df = clean_df(ensure_cols(safe_read_csv(ITEM_MASTER_FILE, item_master_cols), item_master_cols))
        item_df["BuyPrice"] = pd.to_numeric(item_df["BuyPrice"].astype(str).str.replace(',', ''), errors='coerce').fillna(0.0).astype(float)
        item_df["ListPrice"] = pd.to_numeric(item_df["ListPrice"].astype(str).str.replace(',', ''), errors='coerce').fillna(0.0).astype(float)
        curr_opts = list(set(CURRENCY_OPTIONS + ["-", ""] + [str(x) for x in item_df["Currency"].unique()]))

        for col in ["ItemName", "PartNo", "Description", "Supplier", "Remarks", "Currency"]:
            item_df[col] = item_df[col].fillna("").astype(str)

        with st.container(border=True):
            if not item_df.empty:
                f_col1, f_col2, f_col3 = st.columns([3, 3, 4])
                col_options = [t("all"), "Supplier", "Currency", "ItemName", "PartNo"]
                with f_col1: selected_col = st.selectbox(t("filter_category"), col_options, key="item_filter_cat")
                with f_col2:
                    if selected_col == t("all"): selected_val = st.selectbox(t("filter_value"), [t("all")], disabled=True, key="item_filter_val")
                    else:
                        unique_vals = sorted([str(x) for x in item_df[selected_col].unique() if str(x).strip() and str(x) != "-"])
                        selected_val = st.selectbox(t("filter_value"), [t("all")] + unique_vals, key="item_filter_val")
                with f_col3: keyword = st.text_input(t("filter_keyword"), placeholder=t("filter_keyword_ph"), key="item_kw_search")

                filtered_df = item_df.copy()
                if selected_col != t("all") and selected_val != t("all"):
                    filtered_df = filtered_df[filtered_df[selected_col].astype(str) == selected_val]
                if keyword.strip():
                    kw = keyword.strip().lower()
                    filtered_df = filtered_df[filtered_df.apply(lambda row: row.astype(str).str.lower().str.contains(kw).any(), axis=1)]

                st.markdown(t("total_records", count=len(filtered_df), total=len(item_df)))

                item_config = {
                    "ItemName": st.column_config.TextColumn("Item Name"),
                    "PartNo": st.column_config.TextColumn("Part No"),
                    "Description": st.column_config.TextColumn("Description"),
                    "Supplier": st.column_config.TextColumn("공급사 (Supplier)"),
                    "BuyPrice": st.column_config.NumberColumn("매입가 (Buy Price)", format="%,.2f"),
                    "ListPrice": st.column_config.NumberColumn("매출가 (List Price)", format="%,.2f"),
                    "Currency": st.column_config.SelectboxColumn("통화", options=curr_opts),
                    "Remarks": st.column_config.TextColumn("Remarks"),
                }

                edited_df = st.data_editor(filtered_df, column_config=item_config, num_rows="dynamic", use_container_width=True, key="item_master_editor")

                if st.button("💾 자재 마스터 DB 변경사항 저장", key="btn_save_item_master"):
                    if selected_col == t("all") and not keyword.strip(): updated_master = edited_df
                    else:
                        updated_master = item_df.copy()
                        updated_master.loc[filtered_df.index] = edited_df
                    safe_save_csv(updated_master, ITEM_MASTER_FILE, item_master_cols)
                    st.success("🎉 자재 마스터 DB가 성공적으로 저장되었습니다.")
                    st.rerun()

                st.download_button(t("btn_download_csv"), edited_df.to_csv(index=False, encoding='utf-8-sig'), file_name="item_master_db.csv", mime="text/csv", key="dl_item_master_csv")
            else: st.info("등록된 자재/품목 데이터가 없습니다.")

    elif db_choice == "🤝 통합 거래처 DB (Partners)":
        partner_df = clean_df(ensure_cols(safe_read_csv(PARTNER_DB_FILE, partner_db_cols), partner_db_cols))
        with st.container(border=True):
            st.markdown("### 🤝 고객사 및 공급사 거래처 관리")
            edited_partner_df = st.data_editor(partner_df, num_rows="dynamic", use_container_width=True, key="partner_db_editor")
            if st.button("💾 거래처 DB 저장", key="btn_save_partner_db"):
                safe_save_csv(edited_partner_df, PARTNER_DB_FILE, partner_db_cols)
                st.success("🎉 거래처 DB가 업데이트되었습니다.")
                st.rerun()

    elif db_choice == "🚢 선박/프로젝트 DB (Vessels)":
        vessel_df = clean_df(ensure_cols(safe_read_csv(VESSEL_DB_FILE, vessel_db_cols), vessel_db_cols))
        with st.container(border=True):
            st.markdown("### 🚢 선박 및 프로젝트 Master 관리")
            edited_vessel_df = st.data_editor(vessel_df, num_rows="dynamic", use_container_width=True, key="vessel_db_editor")
            if st.button("💾 선박 DB 저장", key="btn_save_vessel_db"):
                safe_save_csv(edited_vessel_df, VESSEL_DB_FILE, vessel_db_cols)
                st.success("🎉 선박 DB가 업데이트되었습니다.")
                st.rerun()

    else:
        tab_our, tab_cust = st.tabs(["🏢 자사 서류 대장", "🤝 고객사 / 공급사 서류 대장"])
        def render_ledger_tab(db_filepath, tab_key_prefix):
            db_df = clean_df(ensure_cols(safe_read_csv(db_filepath, doc_db_cols), doc_db_cols))
            db_df["TotalAmount"] = pd.to_numeric(db_df["TotalAmount"].astype(str).str.replace(',', ''), errors='coerce').fillna(0.0).astype(float)
            db_df["ItemCount"] = pd.to_numeric(db_df["ItemCount"].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(float)

            doc_type_opts = list(set(["Quotation", "Purchase Order", "Invoice", "Delivery Note", "Service Report", "Credit Note", "-", ""] + [str(x) for x in db_df["DocType"].unique()]))
            curr_opts = list(set(CURRENCY_OPTIONS + ["-", ""] + [str(x) for x in db_df["Currency"].unique()]))
            status_opts = list(set(STATUS_OPTIONS + ["-", ""] + [str(x) for x in db_df["Status"].unique()]))

            for col in ["IssueDate", "DocDate", "OurRef", "YourRef", "ShipName", "TargetName", "CreatedBy", "DocType", "Currency", "Status"]:
                db_df[col] = db_df[col].fillna("-").astype(str)

            status_counts = db_df["Status"].value_counts()
            c_m1, c_m2, c_m3, c_m4, c_m5 = st.columns(5)
            c_m1.metric("🟡 Quoted (견적)", f"{status_counts.get('🟡 Quoted', 0)} 건")
            c_m2.metric("🔵 PO Received (수주)", f"{status_counts.get('🔵 PO Received', 0)} 건")
            c_m3.metric("🟣 Invoiced (청구)", f"{status_counts.get('🟣 Invoiced', 0)} 건")
            c_m4.metric("🟢 Paid (입금완료)", f"{status_counts.get('🟢 Paid', 0)} 건")
            c_m5.metric("🔴 Cancelled (취소)", f"{status_counts.get('🔴 Cancelled', 0)} 건")
            st.markdown("<hr style='margin: 12px 0; border-color: #1E293B;'>", unsafe_allow_html=True)

            if not db_df.empty:
                f_col1, f_col2, f_col3 = st.columns([3, 3, 4])
                valid_cols = ["Status", "DocType", "ShipName", "CreatedBy", "TargetName", "Currency", "OurRef", "YourRef"]
                col_options = [t("all")] + [c for c in valid_cols if c in db_df.columns]
                
                with f_col1: selected_col = st.selectbox(t("filter_category"), col_options, key=f"{tab_key_prefix}_cat")
                with f_col2:
                    if selected_col == t("all"): selected_val = st.selectbox(t("filter_value"), [t("all")], disabled=True, key=f"{tab_key_prefix}_val")
                    else:
                        unique_vals = sorted([str(x) for x in db_df[selected_col].unique() if str(x).strip() and str(x) != "-"])
                        selected_val = st.selectbox(t("filter_value"), [t("all")] + unique_vals, key=f"{tab_key_prefix}_val")
                with f_col3: keyword = st.text_input(t("filter_keyword"), placeholder=t("filter_keyword_ph"), key=f"{tab_key_prefix}_kw")

                filtered_df = db_df.copy()
                if selected_col != t("all") and selected_val != t("all"):
                    filtered_df = filtered_df[filtered_df[selected_col].astype(str) == selected_val]
                if keyword.strip():
                    kw = keyword.strip().lower()
                    filtered_df = filtered_df[filtered_df.apply(lambda row: row.astype(str).str.lower().str.contains(kw).any(), axis=1)]

                st.markdown(t("total_records", count=len(filtered_df), total=len(db_df)))

                ledger_config = {
                    "IssueDate": st.column_config.TextColumn("Issue Date"),
                    "DocDate": st.column_config.TextColumn("Doc Date"),
                    "DocType": st.column_config.SelectboxColumn("Doc Type", options=doc_type_opts),
                    "OurRef": st.column_config.TextColumn("Our Ref"),
                    "YourRef": st.column_config.TextColumn("Your Ref"),
                    "ShipName": st.column_config.TextColumn("Ship Name"),
                    "TargetName": st.column_config.TextColumn("Target Name"),
                    "Currency": st.column_config.SelectboxColumn("Currency", options=curr_opts),
                    "TotalAmount": st.column_config.NumberColumn("Total Amount", format="%,.2f"),
                    "ItemCount": st.column_config.NumberColumn("Item Count", format="%d"),
                    "CreatedBy": st.column_config.TextColumn("Created By"),
                    "Status": st.column_config.SelectboxColumn("▾ Status (파이프라인)", options=status_opts),
                }

                edited_df = st.data_editor(filtered_df, column_config=ledger_config, num_rows="dynamic", use_container_width=True, key=f"{tab_key_prefix}_editor")

                if st.button("💾 변경사항 저장", key=f"btn_save_{tab_key_prefix}"):
                    if selected_col == t("all") and not keyword.strip(): updated_master = edited_df
                    else:
                        updated_master = db_df.copy()
                        updated_master.loc[filtered_df.index] = edited_df
                    safe_save_csv(updated_master, db_filepath, doc_db_cols)
                    st.success("🎉 서류 대장이 성공적으로 저장되었습니다.")
                    st.rerun()

                st.download_button(t("btn_download_csv"), edited_df.to_csv(index=False, encoding='utf-8-sig'), file_name=f"{tab_key_prefix}_ledger.csv", mime="text/csv", key=f"dl_{tab_key_prefix}_csv")
            else: st.info(t("no_ledger"))

        with tab_our: render_ledger_tab(OUR_DB_FILE, "our_doc")
        with tab_cust: render_ledger_tab(CUSTOMER_DB_FILE, "cust_doc")

# ==========================================
# 8. 관리자 메뉴
# ==========================================
elif menu == "관리자 메뉴":
    st.markdown("""<div class="main-header"><h1>🛠️ 관리자 통합 전용 메뉴 (Admin Control)</h1><p>DB 데이터 수정/삭제 및 시스템 저장소/대장 초기화를 수행합니다.</p></div>""", unsafe_allow_html=True)

    if 'admin_unlocked' not in st.session_state: st.session_state['admin_unlocked'] = False

    if not st.session_state['admin_unlocked']:
        with st.container(border=True):
            st.markdown("### 🔒 관리자 인증")
            admin_input_pwd = st.text_input("관리자 비밀번호를 입력하세요", type="password", key="admin_auth_pwd_field")
            if st.button("🔓 인증 및 접속", key="btn_admin_auth_unlock"):
                if admin_input_pwd == ADMIN_PASSWORD:
                    st.session_state['admin_unlocked'] = True
                    st.success("✅ 관리자 권한이 성공적으로 인증되었습니다.")
                    st.rerun()
                else: st.error(t("pwd_err"))
    else:
        col_hdr_a, col_hdr_b = st.columns([8, 2])
        with col_hdr_b:
            if st.button("🔒 관리자 잠금", key="btn_admin_lock"):
                st.session_state['admin_unlocked'] = False
                st.rerun()

        admin_tab1, admin_tab2, admin_tab3, admin_tab4 = st.tabs([
            "🏢 자사 서류 대장 가공", "🤝 고객사 서류 대장 가공", 
            "📦 자재 단가 마스터 가공", "🚨 전체 초기화 및 저장소 관리"
        ])

        with admin_tab1:
            st.markdown("### 🏢 자사 서류 대장 수정 및 삭제")
            our_df_admin = clean_df(ensure_cols(safe_read_csv(OUR_DB_FILE, doc_db_cols), doc_db_cols))
            edited_our_admin = st.data_editor(our_df_admin, num_rows="dynamic", use_container_width=True, key="admin_our_editor")
            col1, col2 = st.columns([1, 1])
            with col1:
                if st.button("💾 자사 서류 대장 변경사항 반영", key="btn_admin_save_our"):
                    safe_save_csv(edited_our_admin, OUR_DB_FILE, doc_db_cols)
                    st.success("✅ 자사 서류 대장이 저장되었습니다.")
                    st.rerun()
            with col2:
                if st.button("🚨 자사 서류 대장 전체 초기화", key="btn_admin_reset_our"):
                    safe_save_csv(pd.DataFrame(columns=doc_db_cols), OUR_DB_FILE, doc_db_cols)
                    st.success("🚨 자사 서류 대장 및 구글 시트가 완전 초기화되었습니다.")
                    st.rerun()

        with admin_tab2:
            st.markdown("### 🤝 고객사 / 공급사 서류 대장 수정 및 삭제")
            cust_df_admin = clean_df(ensure_cols(safe_read_csv(CUSTOMER_DB_FILE, doc_db_cols), doc_db_cols))
            edited_cust_admin = st.data_editor(cust_df_admin, num_rows="dynamic", use_container_width=True, key="admin_cust_editor")
            col1, col2 = st.columns([1, 1])
            with col1:
                if st.button("💾 고객사 서류 대장 변경사항 반영", key="btn_admin_save_cust"):
                    safe_save_csv(edited_cust_admin, CUSTOMER_DB_FILE, doc_db_cols)
                    st.success("✅ 고객사 서류 대장이 저장되었습니다.")
                    st.rerun()
            with col2:
                if st.button("🚨 고객사 서류 대장 전체 초기화", key="btn_admin_reset_cust"):
                    safe_save_csv(pd.DataFrame(columns=doc_db_cols), CUSTOMER_DB_FILE, doc_db_cols)
                    st.success("🚨 고객사 서류 대장 및 구글 시트가 완전 초기화되었습니다.")
                    st.rerun()

        with admin_tab3:
            st.markdown("### 📦 자재 단가 마스터 DB 수정 및 삭제")
            item_df_admin = clean_df(ensure_cols(safe_read_csv(ITEM_MASTER_FILE, item_master_cols), item_master_cols))
            edited_item_admin = st.data_editor(item_df_admin, num_rows="dynamic", use_container_width=True, key="admin_item_editor")
            col1, col2 = st.columns([1, 1])
            with col1:
                if st.button("💾 자재 마스터 DB 변경사항 반영", key="btn_admin_save_item"):
                    safe_save_csv(edited_item_admin, ITEM_MASTER_FILE, item_master_cols)
                    st.success("✅ 자재 마스터 DB가 저장되었습니다.")
                    st.rerun()
            with col2:
                if st.button("🚨 자재 마스터 DB 전체 초기화", key="btn_admin_reset_item"):
                    safe_save_csv(pd.DataFrame(columns=item_master_cols), ITEM_MASTER_FILE, item_master_cols)
                    st.success("🚨 자재 마스터 DB 및 구글 시트가 완전 초기화되었습니다.")
                    st.rerun()

        with admin_tab4:
            st.markdown("### 🚨 저장소 파일 및 인풋/히스토리/임시저장 완전 초기화")
            st.warning("⚠️ 아래 실행 시 삭제된 파일 및 데이터는 복구할 수 없습니다.")
            col_r1, col_r2, col_r3 = st.columns(3)
            with col_r1:
                if st.button("🗑️ 저장된 PDF 및 AI 인풋 파일 전체 삭제", key="btn_admin_clear_files"):
                    for f in os.listdir("output"):
                        if f.endswith('.pdf'):
                            try: os.remove(os.path.join("output", f))
                            except Exception: pass
                    for f in os.listdir(INPUT_DOCS_DIR):
                        try: os.remove(os.path.join(INPUT_DOCS_DIR, f))
                        except Exception: pass
                    st.success("✅ output 및 input_docs 파일이 모두 삭제되었습니다.")
                    st.rerun()

            with col_r2:
                if st.button("🗑️ 선박명/거래처 입력 히스토리 전체 삭제", key="btn_admin_clear_history"):
                    if os.path.exists(HISTORY_FILE):
                        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                            json.dump({"ships": [], "to_list": [], "attns": []}, f, ensure_ascii=False, indent=2)
                    st.success("✅ 자동완성 히스토리가 초기화되었습니다.")
                    st.rerun()

            with col_r3:
                if st.button("🗑️ 사용자 임시저장(Draft) 전체 삭제", key="btn_admin_clear_drafts"):
                    if os.path.exists(DRAFTS_FILE):
                        try: os.remove(DRAFTS_FILE)
                        except Exception: pass
                    st.success("✅ 모든 사용자 작성 임시저장 데이터가 삭제되었습니다.")
                    st.rerun()

# ==========================================
# 9. 서류 이력 & 갤러리
# ==========================================
else:
    st.markdown("""<div class="main-header"><h1>🖼️ 서류 이력 & 갤러리 (Document Gallery & History)</h1><p>생성된 PDF 문서와 AI 분석에 입력된 문서/이미지를 조회하고 다운로드합니다.</p></div>""", unsafe_allow_html=True)
    tab_out, tab_in = st.tabs(["📄 생성 완료된 PDF 서류", "📥 AI 인풋 분석 문서/이미지"])

    with tab_out:
        pdf_files = sorted([f for f in os.listdir("output") if f.endswith('.pdf')], reverse=True)
        if pdf_files:
            cols = st.columns(3)
            for idx, file_name in enumerate(pdf_files):
                with cols[idx % 3]:
                    with st.container(border=True):
                        st.markdown(f"**📑 {file_name}**")
                        pdf_path = os.path.join("output", file_name)
                        pdf_data = open(pdf_path, "rb").read()
                        imgs = render_pdf_images(pdf_data)
                        if imgs: st.image(imgs[0], caption="1페이지 썸네일", use_container_width=True)
                        st.download_button("💾 PDF 다운로드", pdf_data, file_name=file_name, mime="application/pdf", key=f"dl_grid_{idx}")
        else: st.info("발행되어 저장된 PDF 서류가 없습니다.")

    with tab_in:
        input_files = sorted([f for f in os.listdir(INPUT_DOCS_DIR) if os.path.isfile(os.path.join(INPUT_DOCS_DIR, f))], reverse=True)
        if input_files:
            cols_in = st.columns(3)
            for idx, file_name in enumerate(input_files):
                with cols_in[idx % 3]:
                    with st.container(border=True):
                        st.markdown(f"**📥 {file_name}**")
                        file_path = os.path.join(INPUT_DOCS_DIR, file_name)
                        file_bytes = open(file_path, "rb").read()
                        ext = file_name.split('.')[-1].lower()
                        if ext in ['png', 'jpg', 'jpeg']: st.image(file_bytes, caption=file_name, use_container_width=True)
                        st.download_button("💾 파일 다운로드", file_bytes, file_name=file_name, key=f"dl_in_{idx}")
        else: st.info("AI 문서 분석에 업로드된 인풋 문서가 없습니다.")

if is_running:
    time.sleep(1.0)
    st.rerun()
