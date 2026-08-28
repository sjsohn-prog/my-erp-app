import streamlit as st
import pandas as pd
from jinja2 import Environment
import os
import base64
import json
import re
import time
import io
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
import google.generativeai as genai
from PIL import Image
import pymupdf  # PyMuPDF fitz

# 구글 시트 연동 라이브러리 예외 처리
try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False

# ==========================================
# 0. 보안 비밀번호 및 환경 설정
# ==========================================
def get_secret(key, default=""):
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default

ADMIN_PASSWORD = get_secret("ADMIN_PASSWORD")
SAVE_PASSWORD = get_secret("SAVE_PASSWORD")
DEFAULT_GEMINI_KEY = get_secret("GEMINI_API_KEY", "")

FLAG_OPTIONS = [
    "Panama", "Liberia", "Marshall Islands", "Hong Kong", "Singapore", 
    "Korea (KR)", "Bahamas", "Malta", "Cyprus", "India", "China", "Greece", "UK"
]

CLASS_OPTIONS = [
    "ABS", "BV", "CCS", "CRS", "DNV", "IRS", "KR", "LR", 
    "NK", "PRS", "RINA", "TL", "Non-IACS", "KR & NK", "DNV & LR", "IRS & DNV", "Panama / KR"
]

CURRENCY_OPTIONS = ["KRW", "USD", "EUR", "JPY", "CNY", "SGD", "GBP", "HKD", "AED"]
STATUS_OPTIONS = ["🟡 Quoted", "🔵 PO Received", "🟣 Invoiced", "🟢 Paid", "🔴 Cancelled", "⚪ Draft"]

GOOGLE_CLIENT_ID = get_secret("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = get_secret("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = get_secret("REDIRECT_URI")
ALLOWED_DOMAIN = get_secret("ALLOWED_DOMAIN", "1solution.co.kr")

doc_db_cols = [
    "IssueDate", "DocDate", "DocType", "OurRef", "YourRef", 
    "ShipName", "TargetName", "Currency", "TotalAmount", "ItemCount", "CreatedBy", "Status"
]

item_master_cols = [
    "PartNo", "ItemName", "Description", "Supplier", "BuyPrice", "ListPrice", "Currency", "Remarks"
]

OUR_DB_FILE = "our_db.csv"
CUSTOMER_DB_FILE = "customer_db.csv"
ITEM_MASTER_FILE = "item_master.csv"

# ==========================================
# 0-1. 공통 헬퍼 및 DB 캐싱 / 차분 업데이트
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
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as response:
                data = json.loads(response.read().decode('utf-8'))
                rates = data.get("rates", {})
                if rates:
                    for c in CURRENCY_OPTIONS:
                        if c not in rates: rates[c] = fallback_rates.get(c, 1.0)
                    return rates, fetch_time
        except Exception:
            continue
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

# 매번 파일/시트 읽기를 캐싱하여 속도 최적화
@st.cache_data(ttl=300)
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
            if isinstance(data, list): 
                return ensure_cols(pd.DataFrame(data), default_cols)
        except Exception: pass

    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        return pd.DataFrame(columns=default_cols)
    try:
        df = pd.read_csv(filepath)
        return ensure_cols(df, default_cols)
    except Exception:
        return pd.DataFrame(columns=default_cols)

def safe_save_csv(df, filepath, default_cols=None, incremental_row=None):
    if default_cols is None: default_cols = []
    cleaned_df = ensure_cols(clean_df(df), default_cols)
    cleaned_df.to_csv(filepath, index=False)
    
    # 캐시 지워서 최신화 반영
    st.cache_data.clear()

    sheet_title = os.path.splitext(os.path.basename(filepath))[0]
    gc = get_gsheet_client()
    spreadsheet_key = get_secret("SPREADSHEET_KEY")
    if gc and spreadsheet_key:
        try:
            sh = gc.open_by_key(spreadsheet_key)
            try: ws = sh.worksheet(sheet_title)
            except Exception: ws = sh.add_worksheet(title=sheet_title, rows="1000", cols="20")
            
            # 전체 지우기(clear) 대신 차분 append_rows 적용하여 속도 단축
            if incremental_row is not None:
                ws.append_rows([incremental_row])
            else:
                ws.clear()
                ws.update([cleaned_df.columns.values.tolist()] + cleaned_df.fillna("").values.tolist())
        except Exception as e:
            st.warning(f"⚠️ 구글 시트 동기화 주의 (로컬 CSV에 저장됨): {e}")

def render_unified_input(label, current_val, base_options, key_prefix):
    display_label = f"▾ {label}" if not label.startswith("▾") else label
    curr = clean_str(current_val)
    direct_label = "✏️ 직접 입력 / Direct Input"
    
    options = [""]
    if curr and curr not in options and direct_label not in curr:
        options.append(curr)
        
    for item in base_options:
        s_item = clean_str(item)
        if s_item and s_item not in options and direct_label not in s_item:
            options.append(s_item)
            
    options.append(direct_label)
    
    sel_key = f"{key_prefix}_sel"
    txt_key = f"{key_prefix}_txt"
    
    if sel_key not in st.session_state:
        st.session_state[sel_key] = curr if curr in options else ""

    selected = st.selectbox(display_label, options=options, key=sel_key)
    if selected == direct_label:
        return st.text_input(f"{label} (Direct)", key=txt_key)
    else:
        return selected

# ==========================================
# 0-2. Google OAuth 보안 강화 (State 토큰 포함)
# ==========================================
def get_google_auth_url():
    if not GOOGLE_CLIENT_ID or not REDIRECT_URI:
        return None
    
    state_token = secrets.token_urlsafe(16)
    st.session_state['oauth_state'] = state_token

    scopes = [
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "openid"
    ]
    
    params = {
        "client_id": GOOGLE_CLIENT_ID.strip(),
        "redirect_uri": REDIRECT_URI.strip(),
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state_token,
        "access_type": "offline",
        "prompt": "select_account"
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"

def get_google_user_info(code):
    token_url = "https://oauth2.googleapis.com/token"
    payload = urllib.parse.urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID.strip(),
        "client_secret": GOOGLE_CLIENT_SECRET.strip(),
        "redirect_uri": REDIRECT_URI.strip(),
        "grant_type": "authorization_code"
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
# 1. 페이지 설정 및 CSS
# ==========================================
st.set_page_config(page_title="ONE - ERP", layout="wide", page_icon="🚢")

if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
    st.session_state['user_email'] = ""

try: 
    code_param = st.query_params.get("code", None)
    state_param = st.query_params.get("state", None)
except Exception: 
    code_param, state_param = None, None

if code_param and not st.session_state['authenticated']:
    # OAuth CSRF State 토큰 검증
    if state_param and state_param == st.session_state.get('oauth_state'):
        try:
            user_info = get_google_user_info(code_param)
            email = user_info.get("email", "")
            if ALLOWED_DOMAIN and not email.endswith(f"@{ALLOWED_DOMAIN}") and email != "":
                st.error(f"❌ Access Denied: @{ALLOWED_DOMAIN} 계정만 허용됩니다. (시도: {email})")
            else:
                st.session_state['authenticated'] = True
                st.session_state['user_email'] = email
                st.query_params.clear()
                st.rerun()
        except Exception as e:
            st.query_params.clear()
            st.error(f"Google Auth Error: {e}")
    else:
        st.error("❌ OAuth CSRF State 토큰 검증 실패 (잘못된 요청)")
        st.query_params.clear()

if not st.session_state['authenticated']:
    st.write("")
    _, center_col, _ = st.columns([1, 1.5, 1])
    with center_col:
        with st.container(border=True):
            st.markdown("<h2 style='text-align: center;'>🚢 ONE - ERP</h2>", unsafe_allow_html=True)
            auth_url = get_google_auth_url()
            if auth_url:
                st.markdown(f'<a href="{auth_url}" target="_blank" class="google-btn">🔑 Google 로그인</a>', unsafe_allow_html=True)
            else:
                st.warning("⚠️ GOOGLE_CLIENT_ID 또는 REDIRECT_URI가 설정되지 않았습니다.")
    st.stop()

# ==========================================
# 2. PDF & Gemini 생성 도구
# ==========================================
INLINE_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
    @page { size: A4; margin: 25mm 10mm 15mm 10mm; }
    body { font-family: sans-serif; font-size: 8.5pt; }
    .doc-title { font-size: 20pt; font-weight: bold; text-align: right; }
    table { width: 100%; border-collapse: collapse; margin-bottom: 5px; }
    th, td { border: 0.8px solid #000; padding: 4px; vertical-align: middle; }
    .th-bg { background-color: #f0f0f0; font-weight: bold; }
</style>
</head>
<body>
    <div style="float:left;"><span style="font-size:16pt; font-weight:bold; color:#0284C7;">ONE SOLUTION CO., LTD.</span></div>
    <div class="doc-title">{{ doc_title }}</div>
    <div style="clear:both;"></div>
    <hr>
    <table>
        <tr><td class="th-bg">To</td><td>{{ to_name }}</td><td class="th-bg">PIC</td><td>{{ pic }}</td></tr>
        <tr><td class="th-bg">Attn</td><td>{{ attn_name }}</td><td class="th-bg">Date</td><td>{{ date_str }}</td></tr>
        <tr><td class="th-bg">Your Ref</td><td>{{ your_ref }}</td><td class="th-bg">Our Ref</td><td>{{ our_ref }}</td></tr>
        <tr><td class="th-bg">Vessel</td><td>{{ ship_name }}</td><td class="th-bg">Validity</td><td>{{ validity }}</td></tr>
    </table>
    <div style="text-align:right; font-weight:bold;">Currency: {{ currency }}</div>
    <table>
        <thead>
            <tr class="th-bg">
                <th style="width:5%;">No</th>
                <th style="width:55%;">Description</th>
                <th style="width:10%;">Qty</th>
                <th style="width:15%;">Unit Price</th>
                <th style="width:15%;">Amount</th>
            </tr>
        </thead>
        <tbody>
            {% for item in items %}
            <tr>
                <td style="text-align:center;">{{ loop.index }}</td>
                <td><strong>{{ item.ItemName }}</strong><br>{{ item.Description }}</td>
                <td style="text-align:center;">{{ item.Qty }}</td>
                <td style="text-align:right;">{{ item.UnitPriceFormatted }}</td>
                <td style="text-align:right;">{{ item.AmountFormatted }}</td>
            </tr>
            {% endfor %}
            {% if total_amount_str %}
            <tr>
                <td colspan="3"></td>
                <td class="th-bg" style="text-align:center;">Total</td>
                <td style="text-align:right; font-weight:bold;">{{ total_amount_str }}</td>
            </tr>
            {% endif %}
        </tbody>
    </table>
</body>
</html>
"""

def generate_pdf(context):
    from weasyprint import HTML
    env = Environment()
    template = env.from_string(INLINE_HTML_TEMPLATE)
    return HTML(string=template.render(context)).write_pdf()

def extract_ai_data(api_key, content, mode="flash"):
    genai.configure(api_key=api_key)
    model_name = "gemini-3.6-flash-thinking" if mode == "thinking" else "gemini-3.6-flash"
    model = genai.GenerativeModel(model_name)
    prompt = """Extract fields to JSON: doc_type, to_name, attn_name, our_ref, your_ref, ship_name, date_str, currency, items: [{PartNo, ItemName, Description, Qty, UnitPrice, Amount}]"""
    res = model.generate_content([prompt, content])
    text = res.text
    s_idx, e_idx = text.find('{'), text.rfind('}')
    if s_idx != -1 and e_idx != -1:
        return json.loads(text[s_idx:e_idx+1])
    return {}

# ==========================================
# 3. 사이드바 및 레이아웃
# ==========================================
st.sidebar.title("🚢 ONE - ERP")
st.sidebar.markdown(f"👤 접속자: `{st.session_state['user_email']}`")

live_rates, _ = get_exchange_rates()
usd_krw = live_rates.get("KRW", 1350.0)
st.sidebar.metric("🇺🇸 USD / KRW", f"{usd_krw:,.2f} 원")

menu = st.sidebar.radio("SYSTEM MENU", ["서류 분석 / 생성 Master", "서류 관리 대장", "자재 단가 마스터 DB", "관리자 메뉴"])

# ==========================================
# 4. 핵심 기능: 서류 분석 및 생성 Master
# ==========================================
if menu == "서류 분석 / 생성 Master":
    st.title("📄 서류 분석 및 자동 생성 Master")

    # [핵심] @st.fragment 사용으로 입력 시 전체 화면 깜빡임 완벽 방지
    @st.fragment
    def render_doc_editor():
        col_l, col_r = st.columns([5, 5])
        
        with col_l:
            with st.expander("⚡ AI 문서 자동 분석 (클릭)", expanded=False):
                ai_file = st.file_uploader("문서 업로드 (PDF, 이미지)", type=["pdf", "png", "jpg", "jpeg"])
                if ai_file and st.button("✨ 분석 시작"):
                    with st.status("AI가 문서를 분석 중입니다..."):
                        file_bytes = ai_file.getvalue()
                        ext = ai_file.name.split('.')[-1].lower()
                        content = Image.open(io.BytesIO(file_bytes)) if ext in ['png', 'jpg', 'jpeg'] else {"mime_type": "application/pdf", "data": file_bytes}
                        ai_res = extract_ai_data(DEFAULT_GEMINI_KEY, content)
                        st.session_state['parsed_data'] = ai_res
                        st.success("분석 완료!")

            parsed = st.session_state.get('parsed_data', {})
            
            to_val = st.text_input("To (수신)", value=parsed.get("to_name", ""))
            attn_val = st.text_input("Attention", value=parsed.get("attn_name", ""))
            ship_val = st.text_input("Ship's Name", value=parsed.get("ship_name", ""))
            our_ref = st.text_input("Our Ref", value=parsed.get("our_ref", ""))
            your_ref = st.text_input("Your Ref", value=parsed.get("your_ref", ""))
            curr_val = st.selectbox("Currency", CURRENCY_OPTIONS, index=0)

            items_data = parsed.get("items", [{"PartNo": "", "ItemName": "", "Description": "", "Qty": 1, "UnitPrice": 0.0, "Amount": 0.0}])
            items_df = pd.DataFrame(items_data)

            st.markdown("### 📦 품목 입력 (엔터 입력 시 이 구역만 즉시 반영됨)")
            # data_editor 변경 시 해당 fragment만 re-render
            edited_items = st.data_editor(items_df, num_rows="dynamic", use_container_width=True, key="doc_items_fragment_editor")

            if st.button("📥 서류 대장에 저장"):
                if not SAVE_PASSWORD:
                    st.error("보안: SAVE_PASSWORD가 st.secrets에 설정되어야 저장 가능합니다.")
                else:
                    pwd = st.text_input("비밀번호", type="password")
                    if pwd == SAVE_PASSWORD:
                        tot_amt = sum([safe_float(r.get('Amount', 0)) for _, r in edited_items.iterrows()])
                        new_row = [get_kst_now().strftime("%Y-%m-%d"), get_kst_now().strftime("%Y-%m-%d"), "Quotation", our_ref, your_ref, ship_val, to_val, curr_val, tot_amt, len(edited_items), st.session_state['user_email'], "🟡 Quoted"]
                        
                        df_curr = safe_read_csv(OUR_DB_FILE, doc_db_cols)
                        updated_df = pd.concat([df_curr, pd.DataFrame([new_row], columns=doc_db_cols)], ignore_index=True)
                        
                        # append_rows 활용 차분 업데이트
                        safe_save_csv(updated_df, OUR_DB_FILE, doc_db_cols, incremental_row=new_row)
                        st.success("✅ 저장 완료!")

        with col_r:
            st.markdown("### ⚡ 실시간 PDF 미리보기")
            ctx = {
                "doc_title": "QUOTATION", "to_name": to_val, "attn_name": attn_val, "ship_name": ship_val,
                "our_ref": our_ref, "your_ref": your_ref, "currency": curr_val, "date_str": get_kst_now().strftime("%Y-%m-%d"),
                "pic": st.session_state['user_email'].split('@')[0], "validity": "30 Days",
                "items": edited_items.to_dict("records"), "total_amount_str": f"{curr_val} {sum([safe_float(r.get('Amount', 0)) for _, r in edited_items.iterrows()]):,.2f}"
            }
            try:
                pdf_bytes = generate_pdf(ctx)
                doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
                pix = doc[0].get_pixmap(dpi=130)
                st.image(pix.tobytes("png"), use_container_width=True)
                st.download_button("💾 PDF 다운로드", pdf_bytes, file_name="Quotation.pdf", mime="application/pdf")
            except Exception as e:
                st.info("PDF를 생성할 품목을 입력해 주세요.")

    render_doc_editor()

# ==========================================
# 5. 서류 관리 대장
# ==========================================
elif menu == "서류 관리 대장":
    st.title("📊 서류 통합 관리 대장")
    
    @st.fragment
    def render_ledger_fragment():
        df = safe_read_csv(OUR_DB_FILE, doc_db_cols)
        if not df.empty:
            kw = st.text_input("🔎 검색어 입력 (엔터 시 반응)", key="ledger_search")
            if kw:
                df = df[df.apply(lambda row: row.astype(str).str.lower().str.contains(kw.lower()).any(), axis=1)]
            
            edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True, key="ledger_editor")
            if st.button("💾 변경사항 저장"):
                safe_save_csv(edited_df, OUR_DB_FILE, doc_db_cols)
                st.success("성공적으로 저장되었습니다.")
        else:
            st.info("등록된 내역이 없습니다.")

    render_ledger_fragment()

# ==========================================
# 6. 관리자 메뉴 (보안 강화)
# ==========================================
elif menu == "관리자 메뉴":
    st.title("🛠️ 관리자 메뉴")
    if not ADMIN_PASSWORD:
        st.error("🔒 보안 경고: ADMIN_PASSWORD가 st.secrets 환경변수에 설정되어 있지 않습니다.")
    else:
        input_pwd = st.text_input("관리자 비밀번호", type="password")
        if input_pwd == ADMIN_PASSWORD:
            st.success("인증되었습니다.")
            if st.button("🚨 자사 서류 대장 완전 초기화"):
                safe_save_csv(pd.DataFrame(columns=doc_db_cols), OUR_DB_FILE, doc_db_cols)
                st.success("초기화 완료되었습니다.")
        elif input_pwd:
            st.error("비밀번호가 올바르지 않습니다.")
