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
from datetime import datetime
import google.generativeai as genai
from PIL import Image
from streamlit.runtime.scriptrunner import add_script_run_ctx

# ==========================================
# 0. 보안 비밀번호 및 환경 설정
# ==========================================
ADMIN_PASSWORD = "admin0915"
SAVE_PASSWORD = "0915"
DEFAULT_GEMINI_KEY = ""

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
    "Quoted", "PO Received", "Invoiced", "Paid", "Cancelled", "Draft"
]

def get_secret(key, default=""):
    try:
        if key in st.secrets: return st.secrets[key]
    except Exception: pass
    return default

GOOGLE_CLIENT_ID = get_secret("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = get_secret("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = get_secret("REDIRECT_URI")
ALLOWED_DOMAIN = get_secret("ALLOWED_DOMAIN", "1solution.co.kr")

# 안전한 숫자 변환 헬퍼 함수
def safe_float(val, default=0.0):
    if val is None or pd.isna(val):
        return default
    s = str(val).replace(',', '').strip()
    match = re.search(r"[-+]?\d*\.\d+|\d+", s)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return default
    return default

# ⭐ [EmptyDataError 방어] CSV 파일이 0바이트이거나 비어있어도 안선하게 읽어오는 헬퍼 함수
def safe_read_csv(filepath, default_cols=None):
    if default_cols is None:
        default_cols = []
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        return pd.DataFrame(columns=default_cols)
    try:
        df = pd.read_csv(filepath)
        if df.empty:
            return pd.DataFrame(columns=default_cols)
        return df
    except (pd.errors.EmptyDataError, Exception):
        return pd.DataFrame(columns=default_cols)

# 실시간 환율 정보 조회 함수 (USD 기준 API)
@st.cache_data(ttl=3600)
def get_exchange_rates():
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode('utf-8'))
            rates = data.get("rates", {})
            return rates
    except Exception:
        return {"KRW": 1350.0, "USD": 1.0, "EUR": 0.92, "SGD": 1.35, "JPY": 155.0, "CNY": 7.23}

# ==========================================
# 0-1. i18n 다국어 사전 (KR / EN)
# ==========================================
TRANSLATIONS = {
    "KR": {
        "subtitle": "사내 임직원 전용 서류 관리 시스템",
        "google_login": "🔑 Google 계정으로 로그인",
        "test_login": "🚀 테스트 로그인",
        "logout": "🚪 로그아웃",
        "user_label": "👤 접속자:",
        "sys_menu": "SYSTEM MENU",
        "menu_gen": "서류 통합 생성",
        "menu_ledger": "서류 관리대장",
        "menu_db": "마스터 DB 관리",
        "menu_history": "발행 이력 조회",
        "doc_gen_title": "📄 스마트 서류 자동 생성 시스템",
        "doc_gen_desc": "AI 문서 분석을 기반으로 고정 양식 및 마스터 DB 연동 생성을 지원하며, 모든 항목은 직접 수정 가능합니다.",
        "ai_expander_title": "⚡ AI 문서 자동 분석 (클릭하여 열기) 🔽",
        "ai_mode_label": "AI 분석 엔진 선택",
        "mode_flash": "⚡ Gemini 3.6 Flash (고속)",
        "mode_thinking": "🧠 Gemini 3.6 Flash (사고)",
        "upload_doc_label": "문서 업로드 (PDF, JPG, PNG)",
        "btn_ai_parse": "✨ AI 문서 분석",
        "btn_reset": "🔄 서류 입력 초기화",
        "hdr_title": "📌 {doc_type} 헤더 입력 (모든 항목 직접 입력 가능)",
        "items_title": "📦 품목 상세 내역 (줄바꿈/엔터 지원 / 열 너비 자동 맞춤)",
        "remarks_title": "📝 Remarks & Deviations",
        "reg_title": "📌 관리대장 및 DB 등록",
        "pwd_save_label": "🔒 비밀번호",
        "btn_register": "📥 관리대장 및 마스터 DB 등록",
        "preview_title": "⚡ 실시간 PDF 문서 미리보기",
        "btn_download_pdf": "💾 완성된 PDF 다운로드",
        "ledger_title": "📊 서류 발행 관리대장 및 파이프라인 관리",
        "filter_category": "1️⃣ 필터 항목 선택",
        "filter_value": "2️⃣ 하위 값 선택",
        "filter_keyword": "🔎 키워드 통합 검색",
        "filter_keyword_ph": "검색어 입력...",
        "total_records": "**총 `{count}` 건 조회됨** (전체 `{total}` 건 중)",
        "btn_download_csv": "📥 필터링된 결과 엑셀(CSV) 다운로드",
        "no_ledger": "관리대장에 등록된 서류 내역이 없습니다.",
        "ai_db_title": "🤖 AI 단가표 수집기",
        "upload_db_label": "단가표 파일 업로드",
        "parse_mode": "파싱 모드",
        "parse_mode_sheet": "📌 특정 시트 선택",
        "parse_mode_all": "🚀 전체 시트 파싱",
        "select_sheet": "시트 선택",
        "btn_analyze": "✨ 분석",
        "btn_parse_all": "🚀 전체 파싱",
        "btn_final_db_save": "✅ DB 최종 저장",
        "db_mgmt_title": "📊 DB 관리",
        "btn_save_db": "💾 DB 수정사항 저장",
        "db_reset_title": "🚨 DB 초기화",
        "btn_reset": "🔥 초기화",
        "pwd_admin_label": "관리자 비밀번호 입력",
        "pwd_err": "❌ 비밀번호가 올바르지 않습니다.",
        "reg_success": "🎉 서류 관리대장 및 마스터 DB 등록 완료 (작성자: {user})",
        "all": "전체",
        "pwd_ph": "비밀번호 입력..."
    },
    "EN": {
        "subtitle": "In-house Document Management System",
        "google_login": "🔑 Sign in with Google",
        "test_login": "🚀 Test Login",
        "logout": "🚪 Logout",
        "user_label": "👤 User:",
        "sys_menu": "SYSTEM MENU",
        "menu_gen": "Document Generator",
        "menu_ledger": "Document Ledger",
        "menu_db": "Master DB Management",
        "menu_history": "Issue History",
        "doc_gen_title": "📄 Smart Document Generation System",
        "doc_gen_desc": "Supports fixed template & Master DB linked generation. All fields are 100% human-editable.",
        "ai_expander_title": "⚡ AI Document Auto-Analysis (Click to Expand) 🔽",
        "ai_mode_label": "Select AI Engine",
        "mode_flash": "⚡ Gemini 3.6 Flash (Fast)",
        "mode_thinking": "🧠 Gemini 3.6 Flash (Thinking)",
        "upload_doc_label": "Upload Document (PDF, JPG, PNG)",
        "btn_ai_parse": "✨ Analyze Document",
        "btn_reset": "🔄 Reset Form",
        "hdr_title": "📌 {doc_type} Header Details (Direct input supported)",
        "items_title": "📦 Line Item Details (Multi-line supported / Auto-fit)",
        "remarks_title": "📝 Remarks & Deviations",
        "reg_title": "📌 Save to Ledger & Master DB",
        "pwd_save_label": "🔒 Password",
        "btn_register": "📥 Save to Ledger & Master DB",
        "preview_title": "⚡ Live PDF Document Preview",
        "btn_download_pdf": "💾 Download PDF Document",
        "ledger_title": "📊 Document Ledger & Pipeline Management",
        "filter_category": "1️⃣ Select Filter Column",
        "filter_value": "2️⃣ Select Sub-value",
        "filter_keyword": "🔎 Search Keyword",
        "filter_keyword_ph": "Type keyword...",
        "total_records": "**Total `{count}` record(s) found** (Out of `{total}`)",
        "btn_download_csv": "📥 Download Filtered Excel (CSV)",
        "no_ledger": "No document records found in the ledger.",
        "ai_db_title": "🤖 AI Price List Extractor",
        "upload_db_label": "Upload Price List File",
        "parse_mode": "Parsing Mode",
        "parse_mode_sheet": "📌 Select Specific Sheet",
        "parse_mode_all": "🚀 Parse All Sheets",
        "select_sheet": "Select Sheet",
        "btn_analyze": "✨ Analyze",
        "btn_parse_all": "🚀 Parse All",
        "btn_final_db_save": "✅ Save to Master DB",
        "db_mgmt_title": "📊 DB Management",
        "btn_save_db": "💾 Save DB Changes",
        "db_reset_title": "🚨 Reset Master DB",
        "btn_reset": "🔥 Reset DB",
        "pwd_admin_label": "Enter Admin Password",
        "pwd_err": "❌ Incorrect password.",
        "reg_success": "🎉 Saved to Document Ledger & Master DB (Creator: {user})",
        "all": "All",
        "pwd_ph": "Enter password..."
    }
}

def t(key, **kwargs):
    lang = st.session_state.get('lang', 'KR')
    text = TRANSLATIONS.get(lang, TRANSLATIONS['KR']).get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text

# ==========================================
# 0-2. 구글 OAuth 로그인 필수 함수
# ==========================================
def get_google_auth_url():
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile",
        "access_type": "offline",
        "prompt": "select_account"
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"

def get_google_user_info(code):
    token_url = "https://oauth2.googleapis.com/token"
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }).encode('utf-8')
    
    req = urllib.request.Request(token_url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as response:
        token_data = json.loads(response.read().decode('utf-8'))
        
    access_token = token_data.get("access_token")
    userinfo_url = f"https://www.googleapis.com/oauth2/v2/userinfo?access_token={access_token}"
    req_user = urllib.request.Request(userinfo_url)
    with urllib.request.urlopen(req_user) as response_user:
        return json.loads(response_user.read().decode('utf-8'))

# ==========================================
# 1. 페이지 설정 & CSS
# ==========================================
st.set_page_config(page_title="ONE - ERP", layout="wide", page_icon="🚢")

if 'lang' not in st.session_state:
    st.session_state['lang'] = 'KR'

custom_css = """
<style>
    .main-header { background: var(--secondary-background-color); border: 2px solid #0284C7; border-left: 6px solid #0284C7; padding: 16px 20px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
    .main-header h1 { color: var(--text-color); font-size: 1.5rem; font-weight: 800; margin: 0; }
    .main-header p { color: var(--text-color); opacity: 0.85; margin: 4px 0 0 0; font-size: 0.85rem; font-weight: 500; }
    .section-title { color: #0284C7; font-size: 1.05rem; font-weight: 800; margin-bottom: 12px; }
    
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background: var(--secondary-background-color) !important;
        border: 2px solid #0284C7 !important;
        border-radius: 12px !important;
        padding: 16px !important;
        margin-bottom: 16px !important;
        box-shadow: 0 2px 8px rgba(0,0,0,0.15) !important;
    }

    div[data-testid="stExpander"] {
        border: 2px solid #00F0FF !important;
        border-radius: 12px !important;
        background: linear-gradient(135deg, rgba(0, 240, 255, 0.08) 0%, rgba(29, 78, 216, 0.12) 100%) !important;
        box-shadow: 0 0 15px rgba(0, 240, 255, 0.35) !important;
        margin-bottom: 20px !important;
        transition: all 0.3s ease;
    }
    div[data-testid="stExpander"]:hover {
        box-shadow: 0 0 22px rgba(0, 240, 255, 0.6) !important;
        border-color: #38BDF8 !important;
    }
    div[data-testid="stExpander"] summary p {
        font-size: 1.1rem !important;
        font-weight: 800 !important;
        color: #00F0FF !important;
        text-shadow: 0 0 10px rgba(0, 240, 255, 0.5) !important;
    }

    div[data-baseweb="select"] div, div[data-baseweb="input"] input {
        color: #CBD5E1 !important;
        font-weight: 500 !important;
    }
    div[data-baseweb="select"] {
        border-radius: 8px !important;
    }

    .stButton > button, .google-btn { 
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        width: 100% !important; 
        background: linear-gradient(135deg, #1D4ED8 0%, #0284C7 100%) !important; 
        color: #FFFFFF !important; 
        font-weight: 700 !important; 
        border: none !important; 
        padding: 8px 16px !important; 
        border-radius: 8px !important; 
        font-size: 0.95rem !important; 
        text-decoration: none !important;
        box-sizing: border-box !important;
        height: 42px !important;
        margin-bottom: 12px !important;
    }
    .google-btn:hover { opacity: 0.9 !important; color: #FFFFFF !important; }
    .stButton > button:disabled { background: #64748B !important; color: #F1F5F9 !important; cursor: not-allowed !important; }
    .total-badge { background: var(--secondary-background-color); border: 2px solid #0284C7; padding: 12px 16px; border-radius: 10px; text-align: right; font-size: 1.15rem; font-weight: 800; color: #0284C7; margin-top: 10px; }
    .total-subbadge { background: rgba(2, 132, 199, 0.1); border: 1px dashed #0284C7; padding: 8px 12px; border-radius: 8px; text-align: right; font-size: 0.95rem; font-weight: 700; color: #38BDF8; margin-top: 6px; }
    .loader-container { display: flex; align-items: center; justify-content: center; background: var(--secondary-background-color); border: 2px solid #0284C7; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    .spinner { border: 4px solid rgba(2, 132, 199, 0.2); border-top: 4px solid #0284C7; border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; margin-right: 12px; }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    .loader-text { color: var(--text-color); font-weight: 700; font-size: 1rem; }
    .rate-card { background: rgba(15, 23, 42, 0.6); border: 1px solid #1E293B; border-radius: 8px; padding: 8px 10px; margin-bottom: 12px; font-size: 0.8rem; color: #94A3B8; }
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

top_l_col, top_r_col = st.columns([8.5, 1.5])
with top_r_col:
    selected_lang = st.radio("Language", ["KR", "EN"], index=0 if st.session_state['lang'] == 'KR' else 1, horizontal=True, label_visibility="collapsed")
    if selected_lang != st.session_state['lang']:
        st.session_state['lang'] = selected_lang
        st.rerun()

if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
    st.session_state['user_email'] = ""

if 'processed_code' not in st.session_state:
    st.session_state['processed_code'] = None

query_params = st.query_params
if "code" in query_params and not st.session_state['authenticated']:
    auth_code = query_params["code"]
    if st.session_state['processed_code'] == auth_code:
        st.query_params.clear()
    else:
        st.session_state['processed_code'] = auth_code
        try:
            user_info = get_google_user_info(auth_code)
            email = user_info.get("email", "")
            if ALLOWED_DOMAIN and not email.endswith(f"@{ALLOWED_DOMAIN}") and email != "":
                st.error(f"❌ Access Denied: Only @{ALLOWED_DOMAIN} accounts are allowed. (Attempted: {email})")
                st.query_params.clear()
            else:
                st.session_state['authenticated'] = True
                st.session_state['user_email'] = email
                st.query_params.clear()
                st.rerun()
        except Exception as e:
            st.query_params.clear()
            st.error(f"Google Auth Error: {e}")

if not st.session_state['authenticated']:
    st.write("")
    st.write("")
    st.write("")
    _, center_col, _ = st.columns([1, 1.5, 1])
    
    with center_col:
        with st.container(border=True):
            st.markdown("<h2 style='text-align: center; margin-top: 10px; margin-bottom: 4px; font-weight: 800;'>🚢 ONE - ERP</h2>", unsafe_allow_html=True)
            st.markdown(f"<p style='text-align: center; font-size: 0.88rem; color: #94A3B8; margin-bottom: 24px;'>{t('subtitle')}</p>", unsafe_allow_html=True)
            
            if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
                auth_url = get_google_auth_url()
                st.markdown(f'<a href="{auth_url}" target="_self" class="google-btn">{t("google_login")}</a>', unsafe_allow_html=True)

            if st.button(t("test_login")):
                st.session_state['authenticated'] = True
                st.session_state['user_email'] = f"sjsohn@{ALLOWED_DOMAIN}"
                st.rerun()
    st.stop()

# ==========================================
# 2. 내장형 PDF HTML 템플릿
# ==========================================
INLINE_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700&display=swap');
    @page { size: A4; margin: 5mm 5mm; }
    body { font-family: 'Noto Sans KR', 'Malgun Gothic', 'Nanum Gothic', sans-serif; font-size: 8.5pt; line-height: 1.25; color: #000; }
    .title { text-align: center; font-size: 20pt; font-weight: bold; text-decoration: underline; margin-bottom: 14px; text-transform: uppercase; }
    table { width: 100%; border-collapse: collapse; margin-bottom: 8px; }
    th, td { border: 1.5px solid #000; padding: 4px 6px; vertical-align: middle; }
    .hdr-label { width: 15%; font-weight: bold; font-size: 8.5pt; background-color: #f4f4f4; }
    .hdr-value { width: 35%; font-size: 8.5pt; }
    .currency { text-align: right; font-weight: bold; font-style: italic; margin-bottom: 4px; font-size: 8.5pt; }
    .item-th { font-weight: bold; text-align: center; background-color: #f4f4f4; font-size: 8.5pt; }
    .col-no { width: 5%; text-align: center; }
    .col-desc { width: 55%; white-space: pre-line; word-break: break-word; }
    .col-qty { width: 8%; text-align: center; }
    .col-price { width: 16%; text-align: right; }
    .col-amt { width: 16%; text-align: right; }
    .remarks-box { border: 1.5px solid #000; padding: 8px; min-height: 60px; margin-top: 8px; font-size: 8.5pt; white-space: pre-line; }
</style>
</head>
<body>
    {% if logo_base64 %}
    <div style="text-align: left; margin-bottom: 10px;">
        <img src="data:image/png;base64,{{ logo_base64 }}" style="max-height: 55px;" />
    </div>
    {% endif %}
    
    <div class="title">{{ doc_title }}</div>
    
    <table>
        <tr>
            <td class="hdr-label">To</td><td class="hdr-value">{{ to_name }}</td>
            <td class="hdr-label">Our Ref. No.</td><td class="hdr-value">{{ our_ref }}</td>
        </tr>
        <tr>
            <td class="hdr-label">Attention</td><td class="hdr-value">{{ attn_name }}</td>
            <td class="hdr-label">Date</td><td class="hdr-value">{{ date_str }}</td>
        </tr>
        <tr>
            <td class="hdr-label">Project Title</td><td class="hdr-value" colspan="3">{{ project_title }}</td>
        </tr>
        <tr>
            <td class="hdr-label">Validity</td><td class="hdr-value">{{ validity }}</td>
            <td class="hdr-label">Your Ref. No.</td><td class="hdr-value">{{ your_ref }}</td>
        </tr>
        <tr>
            <td class="hdr-label">PIC</td><td class="hdr-value">{{ pic }}</td>
            <td class="hdr-label">Payment Due</td><td class="hdr-value">{{ payment_due }}</td>
        </tr>
        <tr>
            <td class="hdr-label">Ship's Name</td><td class="hdr-value">{{ ship_name }}</td>
            <td class="hdr-label">Flag / Class</td><td class="hdr-value">{{ flag_class }}</td>
        </tr>
    </table>

    <div class="currency">Currency: {{ currency }}</div>
    
    <table>
        <thead>
            <tr>
                <td class="item-th col-no">No.</td>
                <td class="item-th col-desc">Description (Model, Type, Serial No.)</td>
                <td class="item-th col-qty">Q'ty</td>
                <td class="item-th col-price">Unit Price</td>
                <td class="item-th col-amt">Amount</td>
            </tr>
        </thead>
        <tbody>
            {% for item in items %}
            <tr>
                <td class="col-no">{{ loop.index }}</td>
                <td class="col-desc">{% if item.ItemName %}<strong>{{ item.ItemName | replace('\n', '<br>') }}</strong><br>{% endif %}{% if item.Description and item.Description != item.ItemName %}{{ item.Description | replace('\n', '<br>') }}<br>{% endif %}{% if item.Remarks %}<span style="font-size: 8pt; color: #444;"><em>{{ item.Remarks | replace('\n', '<br>') }}</em></span>{% endif %}</td>
                <td class="col-qty">{{ item.Qty }}</td>
                <td class="col-price">{{ item.UnitPriceFormatted }}</td>
                <td class="col-amt">{{ item.AmountFormatted }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>

    {% if bottom_remarks %}
    <div class="remarks-box">
        <strong>[Remarks & Deviations]</strong><br>
        {{ bottom_remarks | replace('\n', '<br>') }}
    </div>
    {% endif %}
</body>
</html>
"""

# ==========================================
# 3. 환경 및 데이터 정제 도구
# ==========================================
KEY_FILE = "gemini_key.txt"
DB_FILE = "master_db.csv"
HISTORY_FILE = "master_history.json"
LEDGER_FILE = "doc_ledger.csv"
os.makedirs("output", exist_ok=True)

def clean_df(df):
    if df is None or df.empty: return df
    df = df.copy().fillna("")
    for col in df.columns:
        df[col] = df[col].astype(str).replace(["nan", "NaN", "None", "null", "<NA>", "none", "None.0", "nan.0"], "")
    return df

def prepare_items_for_pdf(items_list):
    formatted_items = []
    for item in items_list:
        item_copy = dict(item)
        iname = clean_str(item_copy.get('ItemName', ''))
        desc = clean_str(item_copy.get('Description', ''))
        rem = clean_str(item_copy.get('Remarks', ''))
        
        item_copy['ItemName'] = iname
        item_copy['Description'] = desc
        item_copy['Remarks'] = rem
        
        qty_raw = item_copy.get('Qty', '')
        qty_str = str(qty_raw).strip() if qty_raw is not None else ''
        if qty_str in ['', 'nan', 'NaN', 'None', 'null', '<NA>', 'none']:
            item_copy['Qty'] = ''
        else:
            q_val = safe_float(qty_str, default=None)
            if q_val is not None:
                item_copy['Qty'] = f"{int(q_val)}" if q_val == int(q_val) else f"{q_val}"
            else:
                item_copy['Qty'] = qty_str

        u_p_val = safe_float(item_copy.get('UnitPrice', 0))
        amt_val = safe_float(item_copy.get('Amount', 0))
            
        item_copy['UnitPriceFormatted'] = f"{u_p_val:,.0f}" if u_p_val > 0 else ""
        if amt_val > 0:
            item_copy['AmountFormatted'] = f"{amt_val:,.0f}"
        elif amt_val == 0 and u_p_val > 0:
            item_copy['AmountFormatted'] = "0"
        else:
            item_copy['AmountFormatted'] = ""
            
        formatted_items.append(item_copy)
    return formatted_items

if 'bg_task' not in st.session_state:
    st.session_state['bg_task'] = {'status': 'idle', 'type': None, 'progress_msg': '', 'result': None, 'error_msg': None}

is_running = (st.session_state['bg_task']['status'] == 'running')

def load_saved_key():
    try:
        if "GEMINI_API_KEY" in st.secrets: return st.secrets["GEMINI_API_KEY"]
    except Exception: pass
    if DEFAULT_GEMINI_KEY.strip(): return DEFAULT_GEMINI_KEY.strip()
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            k = f.read().strip()
            if k: return k
    return ""

gemini_key = load_saved_key()

# 안전한 CSV 초기화 (EmptyDataError 방지)
db_cols = ["PartNo", "ItemName", "Description", "UnitPrice", "Remarks"]
db_init = safe_read_csv(DB_FILE, db_cols)
if "Category" in db_init.columns and "PartNo" not in db_init.columns: db_init = db_init.rename(columns={"Category": "PartNo"})
for req in db_cols:
    if req not in db_init.columns: db_init[req] = "" if req != "UnitPrice" else 0.0
clean_df(db_init[db_cols]).to_csv(DB_FILE, index=False)

ledger_cols = ["IssueDate", "DocDate", "DocType", "Status", "YourRef", "OurRef", "ShipName", "TargetName", "Currency", "TotalAmount", "ItemCount", "CreatedBy"]
ledger_init = safe_read_csv(LEDGER_FILE, ledger_cols)
clean_df(ledger_init).to_csv(LEDGER_FILE, index=False)

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {"ships": [], "to_list": [], "attns": []}

def save_history(ship, to, attn):
    data = load_history()
    updated = False
    for key, val in [("ships", ship), ("to_list", to), ("attns", attn)]:
        if val and val.strip() and val not in data[key]:
            data[key].append(val.strip())
            updated = True
    if updated:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def save_to_ledger(doc_type, your_ref, our_ref, ship_name, target_name, doc_date_str, currency, total_amount, item_count, user_email=""):
    ledger_df = safe_read_csv(LEDGER_FILE, ledger_cols)
    
    if not ledger_df.empty:
        if "Date" in ledger_df.columns and "DocDate" not in ledger_df.columns:
            ledger_df = ledger_df.rename(columns={"Date": "DocDate"})
        if "IssueDate" not in ledger_df.columns:
            ledger_df.insert(0, "IssueDate", ledger_df.get("DocDate", datetime.now().strftime("%Y-%m-%d")))
        if "Status" not in ledger_df.columns:
            ledger_df.insert(3, "Status", "Quoted")

    issue_date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    doc_date_str = doc_date_str or "-"
    logged_user = user_email or st.session_state.get('user_email', 'Unknown')

    if doc_type == "Quotation": default_status = "Quoted"
    elif doc_type == "Purchase Order": default_status = "PO Received"
    elif doc_type == "Invoice": default_status = "Invoiced"
    else: default_status = "Quoted"

    new_entry = pd.DataFrame([{
        "IssueDate": issue_date_str,
        "DocDate": doc_date_str,
        "DocType": doc_type,
        "Status": default_status,
        "YourRef": your_ref or "-",
        "OurRef": our_ref or "-",
        "ShipName": ship_name or "-",
        "TargetName": target_name or "-",
        "Currency": currency or "-", 
        "TotalAmount": total_amount,
        "ItemCount": item_count,
        "CreatedBy": logged_user
    }])

    updated_df = pd.concat([ledger_df, new_entry], ignore_index=True)
    for c in ledger_cols:
        if c not in updated_df.columns: updated_df[c] = "-"
    
    updated_df[ledger_cols].to_csv(LEDGER_FILE, index=False)

def safe_merge_db(existing_db, new_data_df):
    if new_data_df is None or new_data_df.empty: return existing_db
    combined = pd.concat([existing_db, new_data_df], ignore_index=True)
    for col in ['PartNo', 'ItemName', 'Description', 'UnitPrice', 'Remarks']:
        if col not in combined.columns: combined[col] = '' if col != 'UnitPrice' else 0.0
    has_pno = combined['PartNo'].astype(str).str.strip() != ""
    has_item = combined['ItemName'].astype(str).str.strip() != ""
    has_desc = combined['Description'].astype(str).str.strip() != ""
    res = combined[has_pno | has_item | has_desc].drop_duplicates(subset=['PartNo', 'ItemName', 'Description'], keep='last')
    return clean_df(res)

if 'doc_info' not in st.session_state:
    st.session_state['doc_info'] = {"to": "", "attn": "", "project_title": "", "validity": "", "flag_class": "", "our_ref": "", "date": "", "pic": "", "your_ref": "", "ship": "", "payment_due": "", "currency": "", "bottom_remarks": ""}

if 'doc_items' not in st.session_state:
    st.session_state['doc_items'] = pd.DataFrame([{"PartNo": "", "ItemName": "", "Description": "", "Qty": "", "UnitPrice": 0.0, "Amount": 0.0, "Remarks": ""}])

def generate_pdf(context):
    from weasyprint import HTML
    logo_path = os.path.abspath("logo.png")
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f: context["logo_base64"] = base64.b64encode(f.read()).decode('utf-8')
    else: context["logo_base64"] = None
    
    env = Environment()
    template = env.from_string(INLINE_HTML_TEMPLATE)
    html_out = template.render(context)
    
    pdf_bytes = HTML(string=html_out).write_pdf()
    return pdf_bytes

def render_pdf_images(pdf_bytes):
    images = []
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            images.append(pix.tobytes("png"))
    except Exception: pass
    return images

def clean_str(val):
    if pd.isna(val) or val is None: return ""
    s = str(val).strip()
    return "" if s.lower() in ['nan', 'none', 'null', '<na>', 'nan.0', 'none.0'] else s

def render_unified_input(label, current_val, base_options, key_prefix):
    display_label = f"▾ {label}" if not label.startswith("▾") else label
    curr = clean_str(current_val)
    direct_label = "✏️ 직접 입력 / Direct Input"
    
    options = [""]
    if curr and curr not in options and direct_label not in curr and "직접 입력" not in curr:
        options.append(curr)
        
    for item in base_options:
        s_item = clean_str(item)
        if s_item and s_item not in options and direct_label not in s_item and "직접 입력" not in s_item and "Choose an option" not in s_item:
            options.append(s_item)
            
    options.append(direct_label)
    
    sel_key = f"{key_prefix}_sel"
    txt_key = f"{key_prefix}_txt"
    
    if sel_key not in st.session_state:
        st.session_state[sel_key] = curr if curr in options else ""
    elif st.session_state[sel_key] not in options:
        options.insert(1, st.session_state[sel_key])

    selected = st.selectbox(display_label, options=options, key=sel_key)
    
    if selected == direct_label:
        val = st.text_input(f"{label} ({'직접 입력' if st.session_state.get('lang') == 'KR' else 'Direct Input'})", key=txt_key)
        return val
    else:
        return selected

# ==========================================
# 4. AI 파싱 엔진 (Gemini 3.6 Flash 모델 고정)
# ==========================================
def get_ai_response(api_key, content_list, mode="flash"):
    if not api_key or not str(api_key).strip():
        raise Exception("Gemini API Key가 누락되었습니다.")
    genai.configure(api_key=api_key.strip())
    
    if mode == "thinking":
        candidate_models = ['gemini-3.6-flash', 'gemini-3.6-flash-thinking', 'gemini-2.5-flash', 'gemini-1.5-flash']
    else:
        candidate_models = ['gemini-3.6-flash', 'gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash']

    last_err = None
    for model_name in candidate_models:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(content_list)
            if response and response.text:
                res_text = response.text.strip()
                s_idx = res_text.find('[') if '[' in res_text and (res_text.find('[') < res_text.find('{') or '{' not in res_text) else res_text.find('{')
                e_idx = res_text.rfind(']') if ']' in res_text and (res_text.rfind(']') > res_text.rfind('}') or '}' not in res_text) else res_text.rfind('}')
                if s_idx != -1 and e_idx != -1: res_text = res_text[s_idx:e_idx + 1]
                return json.loads(res_text)
        except Exception as e:
            last_err = e
            continue
    raise Exception(f"AI 모델 호출 실패: {last_err}")

def run_bg_doc_parse(task_state, api_key, file_bytes, file_type, doc_type, ai_mode):
    try:
        task_state['status'] = 'running'
        mode_label = "Gemini 3.6 Flash (사고)" if ai_mode == "thinking" else "Gemini 3.6 Flash (고속)"
        task_state['progress_msg'] = f'AI [{mode_label}] 엔진이 문서를 분석 중입니다...'
        
        prompt = f"""
        Extract document details into JSON format matching the fixed header fields and item list.
        
        CRITICAL RULES FOR EXTRACTION:
        1. ISSUER & RECIPIENT DETAILS:
           - "issuer_company": Name of the company issuing/sending this document.
           - "issuer_pic": Person Name / Contact PIC of the issuing company.
           - "recipient_company": Name of the company to whom this doc is addressed (To).
           - "recipient_attn": Person Name specified in "Attention" / "Attn" of this document.

        2. HEADER FIELDS EXTRACTION:
           - "to_name", "attn_name", "project_title", "validity", "flag_class", "our_ref", "date_str", "pic", "your_ref", "ship_name", "payment_due".

        3. ITEM TABLE EXTRACTION & GROUPING RULE:
           - Parse line items into: "PartNo", "ItemName", "Description", "Qty", "UnitPrice", "Amount", "Remarks".
           - CRITICAL GROUPING RULE: When sub-items, breakdown fees, or charges belong to a main category or item, DO NOT split them into separate rows. Combine them into a single row's "Description" or "ItemName" using line breaks (\\n).

        Extract details into valid JSON EXACTLY matching this structure:
        {{
            "issuer_company": "",
            "issuer_pic": "",
            "recipient_company": "",
            "recipient_attn": "",
            "to_name": "",
            "attn_name": "",
            "project_title": "",
            "validity": "",
            "flag_class": "",
            "our_ref": "",
            "date_str": "",
            "pic": "",
            "your_ref": "",
            "ship_name": "",
            "payment_due": "",
            "currency": "",
            "items": [
                {{
                    "PartNo": "", 
                    "ItemName": "", 
                    "Description": "", 
                    "Qty": "", 
                    "UnitPrice": 0.0, 
                    "Amount": 0.0, 
                    "Remarks": ""
                }}
            ]
        }}
        Return ONLY raw JSON.
        """
        content = Image.open(io.BytesIO(file_bytes)) if file_type in ['png', 'jpg', 'jpeg'] else {"mime_type": "application/pdf", "data": file_bytes}
        ai_data = get_ai_response(api_key, [prompt, content], mode=ai_mode)
        task_state['result'] = {'doc_type': doc_type, 'ai_data': ai_data}
        task_state['status'] = 'completed'
    except Exception as e:
        task_state['status'] = 'error'
        task_state['error_msg'] = str(e)

def run_bg_sheet_parse(task_state, api_key, excel_bytes, sheet_names, ai_mode):
    try:
        task_state['status'] = 'running'
        all_results = []
        excel_file = pd.ExcelFile(io.BytesIO(excel_bytes))
        for idx, s_name in enumerate(sheet_names):
            task_state['progress_msg'] = f"[{idx+1}/{len(sheet_names)}] '{s_name}' 시트 추출 중..."
            try:
                df_clean = pd.read_excel(excel_file, sheet_name=s_name).dropna(how='all').dropna(how='all', axis=1)
                if not df_clean.empty:
                    prompt = f"Extract ALL items from sheet '{s_name}' into JSON Array: [{{\"PartNo\":\"\", \"ItemName\":\"\", \"Description\":\"\", \"UnitPrice\":100.0, \"Remarks\":\"\"}}]."
                    res = get_ai_response(api_key, [prompt, f"CSV:\n{df_clean.to_csv(index=False)}"], mode=ai_mode)
                    if isinstance(res, list): all_results.extend(res)
            except Exception: pass
            
        parsed_df = pd.DataFrame(all_results)
        for col in ['PartNo', 'ItemName', 'Description', 'UnitPrice', 'Remarks']:
            if col not in parsed_df.columns: parsed_df[col] = '' if col != 'UnitPrice' else 0.0
        parsed_df['UnitPrice'] = pd.to_numeric(parsed_df['UnitPrice'], errors='coerce').fillna(0.0)
        task_state['result'] = clean_df(parsed_df)
        task_state['status'] = 'completed'
    except Exception as e:
        task_state['status'] = 'error'
        task_state['error_msg'] = str(e)

def start_bg_thread(target_func, args):
    t = threading.Thread(target=target_func, args=args)
    add_script_run_ctx(t)
    t.start()

# ==========================================
# 5. UI 및 사이드바 (다국어 & 실시간 환율 연동)
# ==========================================
st.sidebar.title("🚢 ONE - ERP")
if st.session_state.get('user_email'):
    st.sidebar.markdown(f"{t('user_label')} `{st.session_state['user_email']}`")
    if st.sidebar.button(t("logout")):
        st.session_state['authenticated'] = False
        st.session_state['user_email'] = ""
        st.rerun()

st.sidebar.markdown("""<div style="background: rgba(2, 132, 199, 0.1); border: 1px solid #0284C7; border-radius: 8px; padding: 10px 12px; text-align: center; margin-bottom: 12px;"><span style="color: #0284C7; font-size: 0.85rem; font-weight: 800;">Powered by Gemini 3.6</span></div>""", unsafe_allow_html=True)

live_rates = get_exchange_rates()
usd_krw = live_rates.get("KRW", 1350.0)
eur_usd = live_rates.get("EUR", 0.92)
eur_krw = usd_krw / eur_usd if eur_usd else 1480.0
sgd_usd = live_rates.get("SGD", 1.35)
sgd_krw = usd_krw / sgd_usd if sgd_usd else 1000.0

st.sidebar.markdown(f"""
<div class="rate-card">
    <strong style="color:#0284C7;">💱 실시간 매매기준율 (Live Rates)</strong><br>
    • <b>USD/KRW:</b> {usd_krw:,.2f} 원<br>
    • <b>EUR/KRW:</b> {eur_krw:,.2f} 원<br>
    • <b>SGD/KRW:</b> {sgd_krw:,.2f} 원
</div>
""", unsafe_allow_html=True)

menu_options = [t("menu_gen"), t("menu_ledger"), t("menu_db"), t("menu_history")]
menu_selection = st.sidebar.radio(t("sys_menu"), menu_options)

if menu_selection == t("menu_gen"): menu = "서류 통합 생성"
elif menu_selection == t("menu_ledger"): menu = "서류 관리대장"
elif menu_selection == t("menu_db"): menu = "마스터 DB 관리"
else: menu = "발행 이력 조회"

task = st.session_state['bg_task']
if is_running:
    st.markdown(f"""<div class="loader-container"><div class="spinner"></div><div class="loader-text">{task['progress_msg']} <br><span style='font-size:0.85rem; color:var(--text-color); opacity:0.75; font-weight:500;'>작업 중에도 다른 메뉴로 자유롭게 이동하실 수 있습니다.</span></div></div>""", unsafe_allow_html=True)
elif task['status'] == 'error': st.error(f"❌ AI Error: {task['error_msg']}")

# ==========================================
# 6. 서류 통합 생성
# ==========================================
if menu == "서류 통합 생성":
    doc_type = st.sidebar.selectbox(
        "📋 " + ("Document Type" if st.session_state['lang'] == "EN" else "서류 유형 선택"), 
        ["Quotation", "Purchase Order", "Invoice", "Delivery Note", "Service Report", "Credit Note"]
    )

    st.markdown(f"""<div class="main-header"><h1>{t('doc_gen_title')} ({doc_type})</h1><p>{t('doc_gen_desc')}</p></div>""", unsafe_allow_html=True)

    db = clean_df(safe_read_csv(DB_FILE, db_cols))

    if task['status'] == 'completed' and task['type'] == 'doc_parse':
        ai_data = task['result']['ai_data']
        
        recip_comp = clean_str(ai_data.get("recipient_company", "")) or clean_str(ai_data.get("to_name", ""))
        recip_attn = clean_str(ai_data.get("recipient_attn", "")) or clean_str(ai_data.get("attn_name", ""))
        issuer_comp = clean_str(ai_data.get("issuer_company", ""))
        issuer_pic = clean_str(ai_data.get("issuer_pic", "")) or clean_str(ai_data.get("pic", ""))

        recipient_check = (recip_comp + " " + recip_attn).lower()
        is_incoming_to_us = any(kw in recipient_check for kw in ["1solution", "원솔루션", "one solution"]) or (ALLOWED_DOMAIN in recipient_check)
        
        if is_incoming_to_us:
            to_field_val = issuer_comp or recip_comp
            attn_field_val = issuer_pic
            pic_field_val = recip_attn if recip_attn else st.session_state.get('user_email', '')
            your_ref_val = clean_str(ai_data.get("our_ref", "")) or clean_str(ai_data.get("your_ref", ""))
            our_ref_val = ""
        else:
            to_field_val = recip_comp
            attn_field_val = recip_attn
            pic_field_val = issuer_pic if issuer_pic else st.session_state.get('user_email', '')
            your_ref_val = clean_str(ai_data.get("your_ref", ""))
            our_ref_val = clean_str(ai_data.get("our_ref", ""))

        project_val = clean_str(ai_data.get("project_title", ""))
        validity_val = clean_str(ai_data.get("validity", ""))
        flag_class_val = clean_str(ai_data.get("flag_class", ""))
        date_val = clean_str(ai_data.get("date_str", datetime.now().strftime("%Y-%m-%d")))
        ship_val = clean_str(ai_data.get("ship_name", ""))
        payment_due_val = clean_str(ai_data.get("payment_due", ""))
        currency_val = clean_str(ai_data.get("currency", "KRW"))

        st.session_state['doc_info'] = {
            "to": to_field_val, 
            "attn": attn_field_val, 
            "project_title": project_val,
            "validity": validity_val, 
            "flag_class": flag_class_val, 
            "our_ref": our_ref_val,
            "date": date_val, 
            "pic": pic_field_val, 
            "your_ref": your_ref_val, 
            "ship": ship_val, 
            "payment_due": payment_due_val,
            "currency": currency_val, 
            "bottom_remarks": st.session_state['doc_info'].get("bottom_remarks", "")
        }

        st.session_state['to_sel'] = to_field_val
        st.session_state['attn_sel'] = attn_field_val
        st.session_state['project_title_sel'] = project_val
        st.session_state['our_ref_sel'] = our_ref_val
        st.session_state['your_ref_sel'] = your_ref_val
        st.session_state['date_sel'] = date_val
        st.session_state['validity_sel'] = validity_val
        st.session_state['payment_due_sel'] = payment_due_val
        st.session_state['pic_sel'] = pic_field_val
        st.session_state['ship_sel'] = ship_val

        if "/" in flag_class_val:
            fc_parts = flag_class_val.split("/", 1)
            st.session_state['flag_sel'] = fc_parts[0].strip()
            st.session_state['class_sel'] = fc_parts[1].strip()
        else:
            st.session_state['flag_sel'] = flag_class_val
            st.session_state['class_sel'] = ""

        st.session_state['currency_sel'] = currency_val
        
        parsed_items = ai_data.get("items", [])
        items_df = pd.DataFrame(parsed_items) if parsed_items else pd.DataFrame()
        if not items_df.empty:
            for req_col in ["PartNo", "ItemName", "Description", "Qty", "UnitPrice", "Amount", "Remarks"]:
                if req_col not in items_df.columns:
                    items_df[req_col] = "" if req_col not in ["UnitPrice", "Amount"] else 0.0

            items_df = items_df[["PartNo", "ItemName", "Description", "Qty", "UnitPrice", "Amount", "Remarks"]]

            for idx, row in items_df.iterrows():
                pno = clean_str(row.get('PartNo', ''))
                iname = clean_str(row.get('ItemName', ''))
                desc = clean_str(row.get('Description', ''))
                
                if not iname and desc: iname = desc
                if not desc and iname: desc = iname
                
                items_df.at[idx, 'PartNo'] = pno
                items_df.at[idx, 'ItemName'] = iname
                items_df.at[idx, 'Description'] = desc
                
                match = pd.DataFrame()
                if not db.empty:
                    if pno and 'PartNo' in db.columns: match = db[db['PartNo'] == pno]
                    if match.empty and iname and 'ItemName' in db.columns: match = db[db['ItemName'] == iname]
                    if match.empty and desc and 'Description' in db.columns: match = db[db['Description'] == desc]
                if not match.empty:
                    m = match.iloc[0]
                    if safe_float(row.get('UnitPrice', 0.0)) == 0.0: items_df.at[idx, 'UnitPrice'] = safe_float(m.get('UnitPrice', 0.0))
                    if not pno: items_df.at[idx, 'PartNo'] = clean_str(m.get('PartNo', ''))
                    if not iname: items_df.at[idx, 'ItemName'] = clean_str(m.get('ItemName', ''))
                    if not desc: items_df.at[idx, 'Description'] = clean_str(m.get('Description', ''))
                
                q_raw = clean_str(row.get('Qty', ''))
                qty_val = safe_float(q_raw, default=0.0)
                unit_p_val = safe_float(row.get('UnitPrice', 0.0))
                if safe_float(row.get('Amount', 0.0)) == 0.0:
                    items_df.at[idx, 'Amount'] = qty_val * unit_p_val
            st.session_state['doc_items'] = clean_df(items_df)
        st.session_state['bg_task']['status'] = 'idle'
        st.success("✅ AI Analysis Complete & Roles Auto-Reversed.")

    left_col, right_col = st.columns([5, 5])

    with left_col:
        with st.expander(t("ai_expander_title"), expanded=False):
            ai_mode_choice = st.radio(t("ai_mode_label"), [t("mode_flash"), t("mode_thinking")], horizontal=True, disabled=is_running)
            selected_mode = "thinking" if "Thinking" in ai_mode_choice or "사고" in ai_mode_choice else "flash"
            uploaded_doc = st.file_uploader(t("upload_doc_label"), type=["pdf", "png", "jpg", "jpeg"], disabled=is_running)
            if uploaded_doc and st.button(t("btn_ai_parse"), disabled=is_running):
                st.session_state['bg_task']['type'] = 'doc_parse'
                start_bg_thread(run_bg_doc_parse, (st.session_state['bg_task'], gemini_key, uploaded_doc.getvalue(), uploaded_doc.name.split('.')[-1].lower(), doc_type, selected_mode))
                st.rerun()

        if st.button(t("btn_reset"), disabled=is_running):
            st.session_state['doc_info'] = {"to": "", "attn": "", "project_title": "", "validity": "", "flag_class": "", "our_ref": "", "date": "", "pic": "", "your_ref": "", "ship": "", "payment_due": "", "currency": "", "bottom_remarks": ""}
            st.session_state['doc_items'] = pd.DataFrame([{"PartNo": "", "ItemName": "", "Description": "", "Qty": "", "UnitPrice": 0.0, "Amount": 0.0, "Remarks": ""}])
            
            for key_prefix in ["to", "attn", "project_title", "our_ref", "your_ref", "date", "validity", "payment_due", "pic", "ship", "flag", "class", "currency"]:
                st.session_state[f"{key_prefix}_sel"] = ""
                if f"{key_prefix}_txt" in st.session_state:
                    st.session_state[f"{key_prefix}_txt"] = ""
            st.rerun()

        history = load_history()
        
        with st.container(border=True):
            st.markdown(f'<div class="section-title">{t("hdr_title", doc_type=doc_type)}</div>', unsafe_allow_html=True)
            
            to_name = render_unified_input("To", st.session_state['doc_info'].get("to", ""), history["to_list"], "to")
            attn_name = render_unified_input("Attention", st.session_state['doc_info'].get("attn", ""), history["attns"], "attn")
            project_title = render_unified_input("Project Title", st.session_state['doc_info'].get("project_title", ""), [], "project_title")
            our_ref = render_unified_input("Our Ref. No.", st.session_state['doc_info'].get("our_ref", ""), [], "our_ref")
            your_ref = render_unified_input("Your Ref. No.", st.session_state['doc_info'].get("your_ref", ""), [], "your_ref")
            date_str = render_unified_input("Date", st.session_state['doc_info'].get("date", ""), [datetime.now().strftime("%Y-%m-%d")], "date")
            validity = render_unified_input("Validity", st.session_state['doc_info'].get("validity", ""), ["30 Days", "14 Days", "60 Days", "90 Days"], "validity")
            payment_due = render_unified_input("Payment Due", st.session_state['doc_info'].get("payment_due", ""), ["30 Days Net", "Immediate", "50% Advance / 50% Balance", "60 Days Net"], "payment_due")
            pic_name = render_unified_input("PIC", st.session_state['doc_info'].get("pic", ""), [st.session_state.get('user_email', '')] if st.session_state.get('user_email') else [], "pic")
            ship_name = render_unified_input("Ship's Name", st.session_state['doc_info'].get("ship", ""), history["ships"], "ship")

            curr_fc = clean_str(st.session_state['doc_info'].get("flag_class", ""))
            if "/" in curr_fc:
                fc_parts = curr_fc.split("/", 1)
                curr_flag, curr_class = fc_parts[0].strip(), fc_parts[1].strip()
            else:
                curr_flag, curr_class = curr_fc.strip(), ""

            col_fc1, col_fc2 = st.columns(2)
            with col_fc1:
                sel_flag = render_unified_input("Flag", curr_flag, FLAG_OPTIONS, "flag")
            with col_fc2:
                sel_class = render_unified_input("Class", curr_class, CLASS_OPTIONS, "class")
                
            f_str = sel_flag if sel_flag and "Direct Input" not in sel_flag and "직접 입력" not in sel_flag else ""
            c_str = sel_class if sel_class and "Direct Input" not in sel_class and "직접 입력" not in sel_class else ""
            
            if f_str and c_str:
                flag_class = f"{f_str} / {c_str}"
            elif f_str:
                flag_class = f_str
            else:
                flag_class = c_str

            currency = render_unified_input("Currency", st.session_state['doc_info'].get("currency", ""), CURRENCY_OPTIONS, "currency")

            st.markdown(f'<div class="section-title" style="margin-top:20px;">{t("items_title")}</div>', unsafe_allow_html=True)
            
            df_current = clean_df(st.session_state['doc_items'].copy())
            cols_order = ["PartNo", "ItemName", "Description", "Qty", "UnitPrice", "Amount", "Remarks"]
            for c in cols_order:
                if c not in df_current.columns:
                    df_current[c] = "" if c not in ["UnitPrice", "Amount"] else 0.0
            df_current = clean_df(df_current[cols_order])

            # 수동 행 합치기 / 나누기 도구
            with st.expander("🛠️ 행 합치기 / 나누기 도구 (Merge & Split Rows)", expanded=False):
                m_col1, m_col2 = st.columns(2)
                row_indices = list(range(1, len(df_current) + 1))
                
                with m_col1:
                    st.markdown("**🧩 선택 행 하나로 합치기**")
                    selected_rows_to_merge = st.multiselect("합칠 행 번호 선택 (2개 이상)", options=row_indices, key="merge_rows_select")
                    if st.button("🧩 선택 행 합치기", key="btn_merge_rows"):
                        if len(selected_rows_to_merge) < 2:
                            st.warning("합칠 행을 2개 이상 선택해주세요.")
                        else:
                            zero_idx = [r - 1 for r in selected_rows_to_merge]
                            target_rows = df_current.iloc[zero_idx]
                            
                            merged_pno = next((clean_str(p) for p in target_rows['PartNo'] if clean_str(p)), "")
                            merged_iname = "\n".join([clean_str(i) for i in target_rows['ItemName'] if clean_str(i)])
                            merged_desc = "\n".join([clean_str(d) for d in target_rows['Description'] if clean_str(d)])
                            merged_remarks = "\n".join([clean_str(r) for r in target_rows['Remarks'] if clean_str(r)])
                            
                            merged_qty = sum([safe_float(q) for q in target_rows['Qty'] if safe_float(q) > 0])
                            merged_amt = sum([safe_float(a) for a in target_rows['Amount']])
                            first_u_price = safe_float(target_rows.iloc[0]['UnitPrice'])
                            
                            merged_row = {
                                "PartNo": merged_pno,
                                "ItemName": merged_iname,
                                "Description": merged_desc,
                                "Qty": f"{int(merged_qty)}" if merged_qty == int(merged_qty) and merged_qty > 0 else (f"{merged_qty}" if merged_qty > 0 else ""),
                                "UnitPrice": first_u_price,
                                "Amount": merged_amt,
                                "Remarks": merged_remarks
                            }
                            
                            insert_pos = min(zero_idx)
                            df_remaining = df_current.drop(df_current.index[zero_idx]).reset_index(drop=True)
                            df_top = df_remaining.iloc[:insert_pos]
                            df_bottom = df_remaining.iloc[insert_pos:]
                            
                            new_df = pd.concat([df_top, pd.DataFrame([merged_row]), df_bottom], ignore_index=True)
                            st.session_state['doc_items'] = clean_df(new_df)
                            st.success("선택한 행이 1개로 성공적으로 합쳐졌습니다.")
                            st.rerun()

                with m_col2:
                    st.markdown("**✂️ 선택 행 여러 줄로 나누기**")
                    selected_row_to_split = st.selectbox("나눌 행 번호 선택", options=[None] + row_indices, key="split_row_select")
                    if st.button("✂️ 선택 행 나누기", key="btn_split_row"):
                        if selected_row_to_split is None:
                            st.warning("나눌 행 번호를 선택해주세요.")
                        else:
                            split_target_idx = selected_row_to_split - 1
                            target_row = df_current.iloc[split_target_idx]
                            
                            desc_text = clean_str(target_row['Description'])
                            iname_text = clean_str(target_row['ItemName'])
                            
                            lines = [line.strip() for line in re.split(r'\n|<br>', desc_text if desc_text else iname_text) if line.strip()]
                            
                            if len(lines) <= 1:
                                st.info("해당 행은 줄바꿈이 없거나 1줄이어서 나눌 수 없습니다.")
                            else:
                                split_rows = []
                                for idx_l, line in enumerate(lines):
                                    split_rows.append({
                                        "PartNo": clean_str(target_row['PartNo']) if idx_l == 0 else "",
                                        "ItemName": clean_str(target_row['ItemName']) if idx_l == 0 else "",
                                        "Description": line,
                                        "Qty": clean_str(target_row['Qty']) if idx_l == 0 else "",
                                        "UnitPrice": safe_float(target_row['UnitPrice']) if idx_l == 0 else 0.0,
                                        "Amount": safe_float(target_row['Amount']) if idx_l == 0 else 0.0,
                                        "Remarks": clean_str(target_row['Remarks']) if idx_l == 0 else ""
                                    })
                                
                                df_remaining = df_current.drop(df_current.index[split_target_idx]).reset_index(drop=True)
                                df_top = df_remaining.iloc[:split_target_idx]
                                df_bottom = df_remaining.iloc[split_target_idx:]
                                
                                new_df = pd.concat([df_top, pd.DataFrame(split_rows), df_bottom], ignore_index=True)
                                st.session_state['doc_items'] = clean_df(new_df)
                                st.success(f"행이 {len(lines)}개의 개별 행으로 나누어졌습니다.")
                                st.rerun()

            column_config = {
                "PartNo": st.column_config.TextColumn("PartNo", help="직접 클릭하여 입력/수정"),
                "ItemName": st.column_config.TextColumn("Item Name", help="직접 클릭하여 입력/수정 (줄바꿈 가능)"),
                "Description": st.column_config.TextColumn("Description", help="직접 클릭하여 입력/수정 (줄바꿈 가능)"),
                "Qty": st.column_config.NumberColumn("Q'ty", format="%d", min_value=0),
                "UnitPrice": st.column_config.NumberColumn("Unit Price", format="%,d", min_value=0),
                "Amount": st.column_config.NumberColumn("Amount", format="%,d", min_value=0),
                "Remarks": st.column_config.TextColumn("Remarks", help="직접 클릭하여 입력/수정 (줄바꿈 가능)"),
            }

            for i, row in df_current.iterrows():
                qty = safe_float(row.get('Qty', ''))
                u_price = safe_float(row.get('UnitPrice', 0))
                amt_curr = safe_float(row.get('Amount', 0))
                if amt_curr == 0.0 and u_price > 0:
                    df_current.at[i, 'Amount'] = qty * u_price

            edited_df = st.data_editor(df_current, column_config=column_config, num_rows="dynamic", use_container_width=True)

            edited_df['UnitPrice'] = edited_df['UnitPrice'].apply(safe_float)
            edited_df['Amount'] = edited_df['Amount'].apply(safe_float)

            for i, row in edited_df.iterrows():
                pno = clean_str(row.get('PartNo'))
                iname = clean_str(row.get('ItemName'))
                
                match_row = None
                if pno and not db.empty and 'PartNo' in db.columns and pno in db['PartNo'].values:
                    match_row = db[db['PartNo'] == pno].iloc[0]
                elif iname and not db.empty and 'ItemName' in db.columns and iname in db['ItemName'].values:
                    match_row = db[db['ItemName'] == iname].iloc[0]
                    
                if match_row is not None:
                    if not pno: edited_df.at[i, 'PartNo'] = clean_str(match_row.get('PartNo', ''))
                    if not iname: edited_df.at[i, 'ItemName'] = clean_str(match_row.get('ItemName', ''))
                    if not clean_str(row.get('Description')): edited_df.at[i, 'Description'] = clean_str(match_row.get('Description', ''))
                    
                    u_p_curr = safe_float(row.get('UnitPrice', 0))
                    if u_p_curr == 0.0:
                        u_p = safe_float(match_row.get('UnitPrice', 0.0))
                        edited_df.at[i, 'UnitPrice'] = u_p

            edited_df = clean_df(edited_df)

            if "Amount" in edited_df.columns:
                total_val = edited_df["Amount"].apply(safe_float).sum()
                curr_symbol = currency if currency else "KRW"
                st.markdown(f'<div class="total-badge">Total Amount: {curr_symbol} {total_val:,.2f}</div>', unsafe_allow_html=True)
                
                if curr_symbol == "KRW":
                    converted_val = total_val / usd_krw if usd_krw else 0
                    st.markdown(f'<div class="total-subbadge">💡 Approximate Value in USD: <b>USD ${converted_val:,.2f}</b> (At Rate {usd_krw:,.2f})</div>', unsafe_allow_html=True)
                else:
                    if curr_symbol == "USD": src_rate = 1.0
                    elif curr_symbol == "EUR": src_rate = live_rates.get("EUR", 0.92)
                    elif curr_symbol == "SGD": src_rate = live_rates.get("SGD", 1.35)
                    else: src_rate = 1.0
                    
                    converted_val_krw = (total_val / src_rate) * usd_krw
                    st.markdown(f'<div class="total-subbadge">💡 Approximate Value in KRW: <b>₩ {converted_val_krw:,.0f} 원</b> (At Rate {usd_krw:,.2f})</div>', unsafe_allow_html=True)
            else: total_val = 0.0

            # ⭐ [신규] Remarks & Deviations 및 원클릭 프리셋 툴키트
            st.markdown(f'<div class="section-title" style="margin-top:20px;">{t("remarks_title")}</div>', unsafe_allow_html=True)
            
            preset_col1, preset_col2, preset_col3 = st.columns([1, 1, 1])
            
            TERMS_PRESET = (
                "[Terms & Conditions]\n"
                "1) Payment: T/T remittance within (30) days from the date of invoice\n"
                "2) Shipment: Hand-carry by the service engineer\n"
                "3) Lead-time: Ready in stock\n"
                "4) Warranty: N/A\n"
                "5) Warranty Exceptions: Any damages or faults by user's carelessness\n"
                "6) Drawing: N/A\n"
                "7) Commissioning: N/A"
            )
            
            BANK_PRESET = (
                "[Bank Account Information]\n"
                "* Name of Bank: KEB HANA Bank (CHORYANG Branch)\n"
                "* SWIFT Code: KOEXKRSE\n"
                "* [KRW] Account No.: 322-910016-39004\n"
                "* [USD/EUR/JPY/SGD] Account No.: 322-910008-03738\n"
                "* Beneficiary: One Solution Co., Ltd.\n"
                "► All the banking fees must be paid by remitter without any deduction from the total amount on this invoice."
            )

            with preset_col1:
                if st.button("📋 [Terms & Conditions] 추가", key="btn_preset_terms"):
                    curr_rem = st.session_state['doc_info'].get("bottom_remarks", "")
                    st.session_state['doc_info']["bottom_remarks"] = f"{curr_rem}\n\n{TERMS_PRESET}".strip()
                    st.rerun()
                    
            with preset_col2:
                if st.button("🏦 [Bank Account] 추가", key="btn_preset_bank"):
                    curr_rem = st.session_state['doc_info'].get("bottom_remarks", "")
                    st.session_state['doc_info']["bottom_remarks"] = f"{curr_rem}\n\n{BANK_PRESET}".strip()
                    st.rerun()

            with preset_col3:
                if st.button("🧹 Remarks 내용 비우기", key="btn_preset_clear"):
                    st.session_state['doc_info']["bottom_remarks"] = ""
                    st.rerun()

            bottom_remarks = st.text_area("Remarks", value=st.session_state['doc_info'].get("bottom_remarks", ""), height=120, label_visibility="collapsed", key="txt_bottom_remarks")
            st.session_state['doc_info']["bottom_remarks"] = bottom_remarks
            
            st.markdown(f'<div class="section-title" style="margin-top:20px;">{t("reg_title")}</div>', unsafe_allow_html=True)
            reg_pwd = st.text_input(t("pwd_save_label"), type="password", key="doc_reg_pwd")
            
            if st.button(t("btn_register"), type="secondary", disabled=is_running):
                if reg_pwd != SAVE_PASSWORD:
                    st.error(t("pwd_err"))
                else:
                    current_user_email = st.session_state.get('user_email', 'Unknown')
                    st.session_state['doc_info'] = {"to": to_name, "attn": attn_name, "project_title": project_title, "validity": validity, "flag_class": flag_class, "our_ref": our_ref, "date": date_str, "pic": pic_name, "your_ref": your_ref, "ship": ship_name, "payment_due": payment_due, "currency": currency, "bottom_remarks": bottom_remarks}
                    st.session_state['doc_items'] = clean_df(edited_df)
                    db_items = edited_df[['PartNo', 'ItemName', 'Description', 'UnitPrice', 'Remarks']].copy()
                    db_items['UnitPrice'] = db_items['UnitPrice'].apply(safe_float)
                    safe_merge_db(db, db_items).to_csv(DB_FILE, index=False)
                    
                    save_to_ledger(doc_type, your_ref, our_ref, ship_name, to_name, date_str, currency, total_val, len(edited_df), current_user_email)
                    save_history(ship_name, to_name, attn_name)
                    st.success(t("reg_success", user=current_user_email))

    # 우측 PDF 미리보기 및 AI 이메일 초안 생성
    with right_col:
        with st.container(border=True):
            st.markdown(f'<div class="section-title">{t("preview_title")}</div>', unsafe_allow_html=True)
            
            pdf_formatted_items = prepare_items_for_pdf(clean_df(edited_df).to_dict("records"))
            preview_ctx = {
                "doc_title": doc_type.upper(), "to_name": to_name, "attn_name": attn_name, "project_title": project_title,
                "validity": validity, "flag_class": flag_class, "our_ref": our_ref, "date_str": date_str or datetime.now().strftime("%Y-%m-%d"),
                "pic": pic_name, "your_ref": your_ref, "ship_name": ship_name, "payment_due": payment_due, "currency": currency or "KRW",
                "items": pdf_formatted_items, "bottom_remarks": bottom_remarks
            }
            
            realtime_pdf_bytes = generate_pdf(preview_ctx)
            file_n = f"{doc_type}_{our_ref or your_ref or 'Draft'}.pdf"
            st.download_button(t("btn_download_pdf"), realtime_pdf_bytes, file_name=file_n, mime="application/pdf", key="rt_download")
            
            pdf_imgs = render_pdf_images(realtime_pdf_bytes)
            if pdf_imgs:
                for i, img_b in enumerate(pdf_imgs):
                    st.image(img_b, caption=f"Page {i+1}", use_container_width=True)
            else:
                st.info("Generating PDF preview...")

            is_email_expanded = 'generated_email_body' in st.session_state or st.session_state.get('open_email_expander', False)
            
            with st.expander("📧 AI 영업 이메일 초안 작성 (Email Generator)", expanded=is_email_expanded):
                
                col_em1, col_em2 = st.columns(2)
                with col_em1:
                    email_to = st.text_input("수신인 (To)", value=st.session_state.get('email_to', ''), key="em_to")
                    email_from = st.text_input("발신인 (From)", value=st.session_state.get('user_email', ''), disabled=True, key="em_from")
                with col_em2:
                    email_cc = st.text_input("참조인 (CC)", value=st.session_state.get('email_cc', ''), key="em_cc")
                    default_subj = f"[{doc_type}] {our_ref or your_ref or ship_name} - 1SOLUTION"
                    email_subject = st.text_input("이메일 제목 (Subject)", value=st.session_state.get('email_subject', default_subj), key="em_subject")

                email_lang = st.radio("메일 언어 선택", ["English", "Korean"], horizontal=True, key="email_lang_choice")
                
                if st.button("✨ 영업 이메일 본문 생성", key="btn_gen_email"):
                    st.session_state['open_email_expander'] = True
                    with st.spinner("AI가 비즈니스 메일 초안을 작성 중입니다..."):
                        email_prompt = f"""
                        Write a professional sales email for sending document [{doc_type}].
                        Language: {email_lang}
                        
                        Recipient Details:
                        - To Company: {to_name}
                        - Attention Person: {attn_name}
                        - Vessel Name: {ship_name}
                        - Our Ref No: {our_ref}
                        - Your Ref No: {your_ref}
                        - Total Amount: {currency or 'KRW'} {total_val:,.2f}
                        
                        Tone: Polite, professional, customer-oriented maritime sales style.
                        Keep it concise and clear. Include greeting, document attachment summary, total amount, and professional sign-off.
                        Do NOT include placeholder variables like [Your Name] if PIC is known ({pic_name}).
                        """
                        try:
                            genai.configure(api_key=gemini_key)
                            model = genai.GenerativeModel("gemini-3.6-flash")
                            res = model.generate_content(email_prompt)
                            st.session_state['generated_email_body'] = res.text.strip()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Email Generation Error: {e}")
                
                if 'generated_email_body' in st.session_state:
                    email_body_text = st.text_area("메일 본문 (수정 가능)", value=st.session_state['generated_email_body'], height=220)
                    
                    if not email_to.strip():
                        st.warning("⚠️ 수신인 이메일(To)을 입력해야 메일 앱으로 전송할 수 있습니다.")
                    else:
                        enc_to = urllib.parse.quote(email_to.strip())
                        enc_cc = urllib.parse.quote(email_cc.strip())
                        enc_subj = urllib.parse.quote(email_subject.strip())
                        enc_body = urllib.parse.quote(email_body_text.strip())
                        
                        mailto_url = f"mailto:{enc_to}?cc={enc_cc}&subject={enc_subj}&body={enc_body}"
                        
                        st.markdown(f'<a href="{mailto_url}" target="_blank" class="google-btn" style="text-align:center; display:block;">✉️ 메일 앱으로 전송 (Mailto)</a>', unsafe_allow_html=True)

# ==========================================
# 7. 서류 관리대장 (영업 파이프라인 관리)
# ==========================================
elif menu == "서류 관리대장":
    ledger_df = safe_read_csv(LEDGER_FILE, ledger_cols)
    with st.container(border=True):
        st.markdown(f'<div class="section-title">{t("ledger_title")}</div>', unsafe_allow_html=True)

        if not ledger_df.empty:
            ledger_df = clean_df(ledger_df)
            
            if "Date" in ledger_df.columns and "DocDate" not in ledger_df.columns:
                ledger_df = ledger_df.rename(columns={"Date": "DocDate"})
            if "IssueDate" not in ledger_df.columns:
                ledger_df.insert(0, "IssueDate", ledger_df.get("DocDate", "-"))
            if "Status" not in ledger_df.columns:
                ledger_df.insert(3, "Status", "Quoted")
            if "CreatedBy" not in ledger_df.columns:
                ledger_df["CreatedBy"] = "-"

            f_col1, f_col2, f_col3 = st.columns([3, 3, 4])
            
            valid_cols = ["Status", "DocType", "ShipName", "CreatedBy", "TargetName", "Currency", "IssueDate", "DocDate", "YourRef", "OurRef"]
            col_options = [t("all")] + [c for c in valid_cols if c in ledger_df.columns]
            
            with f_col1:
                selected_col = st.selectbox(t("filter_category"), col_options)

            with f_col2:
                if selected_col == t("all"):
                    sub_options = [t("all")]
                    selected_val = st.selectbox(t("filter_value"), sub_options, disabled=True)
                else:
                    unique_vals = sorted([str(x) for x in ledger_df[selected_col].unique() if str(x).strip() and str(x) != "-"])
                    sub_options = [t("all")] + unique_vals
                    selected_val = st.selectbox(t("filter_value"), sub_options)

            with f_col3:
                keyword = st.text_input(t("filter_keyword"), placeholder=t("filter_keyword_ph"))

            filtered_df = ledger_df.copy()

            if selected_col != t("all") and selected_val != t("all"):
                filtered_df = filtered_df[filtered_df[selected_col].astype(str) == selected_val]

            if keyword.strip():
                kw = keyword.strip().lower()
                match_mask = filtered_df.apply(lambda row: row.astype(str).str.lower().str.contains(kw).any(), axis=1)
                filtered_df = filtered_df[match_mask]

            st.markdown(t("total_records", count=len(filtered_df), total=len(ledger_df)))

            ledger_config = {
                "Status": st.column_config.SelectboxColumn("Status (파이프라인)", options=STATUS_OPTIONS, required=True),
                "IssueDate": st.column_config.TextColumn("Issue Date", disabled=True),
                "DocDate": st.column_config.TextColumn("Doc Date", disabled=True),
                "DocType": st.column_config.TextColumn("Doc Type", disabled=True),
                "YourRef": st.column_config.TextColumn("Your Ref", disabled=True),
                "OurRef": st.column_config.TextColumn("Our Ref", disabled=True),
                "ShipName": st.column_config.TextColumn("Ship Name", disabled=True),
                "TargetName": st.column_config.TextColumn("Target Name", disabled=True),
                "Currency": st.column_config.TextColumn("Currency", disabled=True),
                "TotalAmount": st.column_config.NumberColumn("Total Amount", format="%,.2f", disabled=True),
                "ItemCount": st.column_config.NumberColumn("Item Count", disabled=True),
                "CreatedBy": st.column_config.TextColumn("Created By", disabled=True),
            }

            edited_ledger_df = st.data_editor(filtered_df, column_config=ledger_config, use_container_width=True, key="ledger_editor")

            if st.button("💾 상태(Status) 변경사항 저장"):
                ledger_df.update(edited_ledger_df)
                ledger_df.to_csv(LEDGER_FILE, index=False)
                st.success("🎉 관리대장 상태(Status)가 성공적으로 업데이트되었습니다.")
                st.rerun()

            st.download_button(t("btn_download_csv"), edited_ledger_df.to_csv(index=False, encoding='utf-8-sig'), file_name="ledger_filtered.csv", mime="text/csv")
        else:
            st.info(t("no_ledger"))

# ==========================================
# 8. 마스터 DB 관리
# ==========================================
elif menu == "마스터 DB 관리":
    db = clean_df(safe_read_csv(DB_FILE, db_cols))
    
    if task['status'] == 'completed' and task['type'] == 'db_parse':
        st.session_state['temp_db_upload'] = clean_df(task['result'])
        st.session_state['bg_task']['status'] = 'idle'
        st.success("🎉 AI Parsing Complete")

    with st.container(border=True):
        st.markdown(f'<div class="section-title">{t("ai_db_title")}</div>', unsafe_allow_html=True)
        
        ai_mode_choice_db = st.radio(t("ai_mode_label"), [t("mode_flash"), t("mode_thinking")], horizontal=True, disabled=is_running, key="db_ai_mode")
        selected_mode_db = "thinking" if "Thinking" in ai_mode_choice_db or "사고" in ai_mode_choice_db else "flash"
        uploaded_db_file = st.file_uploader(t("upload_db_label"), type=["xlsx", "csv"], disabled=is_running)
        
        if uploaded_db_file:
            sheet_names = pd.ExcelFile(uploaded_db_file).sheet_names
            parse_mode = st.radio(t("parse_mode"), [t("parse_mode_sheet"), t("parse_mode_all")], horizontal=True, disabled=is_running)
            if parse_mode == t("parse_mode_sheet"):
                selected_sheet = st.selectbox(t("select_sheet"), sheet_names, disabled=is_running)
                if st.button(t("btn_analyze"), disabled=is_running):
                    st.session_state['bg_task']['type'] = 'db_parse'
                    start_bg_thread(run_bg_sheet_parse, (st.session_state['bg_task'], gemini_key, uploaded_db_file.getvalue(), [selected_sheet], selected_mode_db))
                    st.rerun()
            else:
                if st.button(t("btn_parse_all"), disabled=is_running):
                    st.session_state['bg_task']['type'] = 'db_parse'
                    start_bg_thread(run_bg_sheet_parse, (st.session_state['bg_task'], gemini_key, uploaded_db_file.getvalue(), sheet_names, selected_mode_db))
                    st.rerun()

        if 'temp_db_upload' in st.session_state and not st.session_state['temp_db_upload'].empty:
            st.dataframe(st.session_state['temp_db_upload'], use_container_width=True)
            db_parse_pwd = st.text_input(t("pwd_save_label"), type="password", key="db_parse_pwd")
            if st.button(t("btn_final_db_save"), disabled=is_running):
                if db_parse_pwd != SAVE_PASSWORD:
                    st.error(t("pwd_err"))
                else:
                    updated_db = safe_merge_db(db, st.session_state['temp_db_upload'])
                    updated_db.to_csv(DB_FILE, index=False)
                    del st.session_state['temp_db_upload']
                    st.success("Successfully saved to DB.")
                    st.rerun()
    
    with st.container(border=True):
        st.markdown(f'<div class="section-title">{t("db_mgmt_title")}</div>', unsafe_allow_html=True)
        edited_db = clean_df(st.data_editor(db, num_rows="dynamic", use_container_width=True))
        
        db_edit_pwd = st.text_input(t("pwd_save_label"), type="password", key="db_edit_pwd")
        if st.button(t("btn_save_db")):
            if db_edit_pwd != SAVE_PASSWORD:
                st.error(t("pwd_err"))
            else:
                edited_db.to_csv(DB_FILE, index=False)
                st.success("Master DB changes saved successfully.")
    
    with st.container(border=True):
        with st.expander(t("db_reset_title")):
            pwd_input = st.text_input(t("pwd_admin_label"), type="password", key="reset_pwd")
            if st.button(t("btn_reset")) and pwd_input == ADMIN_PASSWORD:
                pd.DataFrame(columns=db_cols).to_csv(DB_FILE, index=False)
                st.success("Master DB initialized.")
                st.rerun()

else:
    files = [f for f in os.listdir("output") if f.endswith('.pdf')]
    if files:
        selected_file = st.selectbox("Select File", files)
        if selected_file:
            pdf_data = open(os.path.join("output", selected_file), "rb").read()
            st.download_button(t("btn_download_pdf"), pdf_data, file_name=selected_file, mime="application/pdf")
            pdf_imgs = render_pdf_images(pdf_data)
            if pdf_imgs:
                for i, img_b in enumerate(pdf_imgs):
                    st.image(img_b, caption=f"Page {i+1}", use_container_width=True)
    else:
        st.info("No saved PDF documents found.")

if is_running:
    time.sleep(1.0)
    st.rerun()
