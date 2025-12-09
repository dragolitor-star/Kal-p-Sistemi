import streamlit as st
import pandas as pd
import re
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import io
import hashlib  # Şifreleme için

# --------------------------------------------------------------------------
# 1. AYARLAR VE DİL SÖZLÜĞÜ
# --------------------------------------------------------------------------
st.set_page_config(page_title="Gerber vs Polypattern", layout="wide")

# Dil Sözlüğü
TRANSLATIONS = {
    "TR": {
        "app_title": "🏭 Kalıp Ölçü Kontrol Sistemi",
        "login_title": "🔐 Giriş Yap",
        "username": "Kullanıcı Adı",
        "password": "Şifre",
        "login_btn": "Giriş Yap",
        "login_success": "Giriş Başarılı!",
        "login_error": "Kullanıcı adı veya şifre hatalı!",
        "logout": "Çıkış Yap",
        "change_pass_title": "🔑 Şifre Değiştir",
        "curr_pass": "Mevcut Şifre",
        "new_pass": "Yeni Şifre",
        "confirm_pass": "Yeni Şifre (Tekrar)",
        "update_pass_btn": "Şifreyi Güncelle",
        "pass_update_success": "Şifreniz başarıyla güncellendi!",
        "pass_update_error": "Şifre güncellenirken hata oluştu.",
        "pass_mismatch": "Yeni şifreler eşleşmiyor!",
        "pass_current_err": "Mevcut şifre hatalı!",
        "menu_header": "Menü",
        "menu_manual": "Yeni Ölçü Kontrolü (Manuel)",
        "menu_excel": "Excel ile Otomatik Kontrol",
        "menu_history": "Kontrol Listesi / Geçmiş",
        "menu_admin": "Kullanıcı Yönetimi (Admin)",
        "bu_select": "Business Unit (BU) Seçiniz",
        "slot_count": "Parça Sayısı",
        "gerber_header": "Gerber Verileri",
        "pp_header": "Polypattern Verisi",
        "analyze_btn": "🔍 Analiz Et",
        "save_list_btn": "Listeye Ekle",
        "finish_save_btn": "Bitir ve Kaydet",
        "save_success": "Kaydedildi!",
        "error_parse": "Veriler okunamadı.",
        "excel_title": "📂 Excel ile Çoklu Model Kontrolü",
        "excel_info": "Dosya içerisinde istediğiniz kadar Gerber ve Polypattern sayfası bulunabilir.",
        "upload_label": "Excel Dosyasını Yükleyin (.xlsx)",
        "analyze_file_btn": "🚀 Dosyayı Analiz Et",
        "reset_btn": "🔄 Sıfırla",
        "save_all_btn": "💾 Tüm Modelleri Kaydet",
        "history_title": "📋 Geçmiş Kayıtlar",
        "search_placeholder": "🔍 Ara (Model, Sezon...)",
        "filter_status": "Durum Filtresi",
        "status_all": "Tümü",
        "status_faulty": "Hatalı",
        "status_correct": "Doğru Çevrilmiş",
        "admin_title": "🛠️ Kullanıcı Yönetimi",
        "add_user_title": "Yeni Kullanıcı Ekle",
        "role_select": "Yetki",
        "create_user_btn": "Kullanıcı Oluştur",
        "user_created": "Kullanıcı başarıyla oluşturuldu.",
        "user_create_err": "Hata oluştu.",
        "delete_user_btn": "Seçili Kullanıcıyı Sil",
        "delete_self_err": "Kendinizi silemezsiniz!",
        "model": "Model",
        "season": "Sezon",
        "part": "Parça",
        "user": "Kullanıcı",
        "date": "Tarih",
        "status": "Durum",
        "faulty_count": "Hatalı Sayısı",
        "max_dev": "Max Sapma",
        "detail": "Detay",
        "result": "Sonuç"
    },
    "ENG": {
        "app_title": "🏭 Pattern Measure Control System",
        "login_title": "🔐 Login",
        "username": "Username",
        "password": "Password",
        "login_btn": "Login",
        "login_success": "Login Successful!",
        "login_error": "Invalid username or password!",
        "logout": "Logout",
        "change_pass_title": "🔑 Change Password",
        "curr_pass": "Current Password",
        "new_pass": "New Password",
        "confirm_pass": "Confirm New Password",
        "update_pass_btn": "Update Password",
        "pass_update_success": "Password updated successfully!",
        "pass_update_error": "Error updating password.",
        "pass_mismatch": "New passwords do not match!",
        "pass_current_err": "Incorrect current password!",
        "menu_header": "Menu",
        "menu_manual": "New Control (Manual)",
        "menu_excel": "Auto Control with Excel",
        "menu_history": "History / Records",
        "menu_admin": "User Management (Admin)",
        "bu_select": "Select Business Unit (BU)",
        "slot_count": "Part Count",
        "gerber_header": "Gerber Data",
        "pp_header": "Polypattern Data",
        "analyze_btn": "🔍 Analyze",
        "save_list_btn": "Add to List",
        "finish_save_btn": "Finish & Save",
        "save_success": "Saved!",
        "error_parse": "Could not parse data.",
        "excel_title": "📂 Multi-Model Control via Excel",
        "excel_info": "File can contain multiple Gerber and Polypattern sheets.",
        "upload_label": "Upload Excel File (.xlsx)",
        "analyze_file_btn": "🚀 Analyze File",
        "reset_btn": "🔄 Reset",
        "save_all_btn": "💾 Save All Models",
        "history_title": "📋 History Records",
        "search_placeholder": "🔍 Search (Model, Season...)",
        "filter_status": "Status Filter",
        "status_all": "All",
        "status_faulty": "Faulty",
        "status_correct": "Correctly Converted",
        "admin_title": "🛠️ User Management",
        "add_user_title": "Add New User",
        "role_select": "Role",
        "create_user_btn": "Create User",
        "user_created": "User created successfully.",
        "user_create_err": "Error occurred.",
        "delete_user_btn": "Delete Selected User",
        "delete_self_err": "You cannot delete yourself!",
        "model": "Model",
        "season": "Season",
        "part": "Part",
        "user": "User",
        "date": "Date",
        "status": "Status",
        "faulty_count": "Fault Count",
        "max_dev": "Max Dev",
        "detail": "Detail",
        "result": "Result"
    },
    "ARB": {
        "app_title": "🏭 نظام مراقبة قياس الأنماط",
        "login_title": "🔐 تسجيل الدخول",
        "username": "اسم المستخدم",
        "password": "كلمة المرور",
        "login_btn": "دخول",
        "login_success": "تم تسجيل الدخول بنجاح!",
        "login_error": "اسم المستخدم أو كلمة المرور غير صحيحة!",
        "logout": "تسجيل الخروج",
        "change_pass_title": "🔑 تغيير كلمة المرور",
        "curr_pass": "كلمة المرور الحالية",
        "new_pass": "كلمة المرور الجديدة",
        "confirm_pass": "تأكيد كلمة المرور الجديدة",
        "update_pass_btn": "تحديث كلمة المرور",
        "pass_update_success": "تم تحديث كلمة المرور بنجاح!",
        "pass_update_error": "حدث خطأ أثناء تحديث كلمة المرور.",
        "pass_mismatch": "كلمات المرور الجديدة غير متطابقة!",
        "pass_current_err": "كلمة المرور الحالية غير صحيحة!",
        "menu_header": "القائمة",
        "menu_manual": "فحص جديد (يدوي)",
        "menu_excel": "فحص تلقائي عبر إكسل",
        "menu_history": "السجل / المحفوظات",
        "menu_admin": "إدارة المستخدمين (مسؤول)",
        "bu_select": "اختر وحدة العمل (BU)",
        "slot_count": "عدد القطع",
        "gerber_header": "بيانات جربر",
        "pp_header": "بيانات بولي باترن",
        "analyze_btn": "🔍 تحليل",
        "save_list_btn": "إضافة للقائمة",
        "finish_save_btn": "إنهاء وحفظ",
        "save_success": "تم الحفظ!",
        "error_parse": "تعذر قراءة البيانات.",
        "excel_title": "📂 فحص متعدد النماذج عبر إكسل",
        "excel_info": "يمكن أن يحتوي الملف على أوراق متعددة.",
        "upload_label": "تحميل ملف إكسل (.xlsx)",
        "analyze_file_btn": "🚀 تحليل الملف",
        "reset_btn": "🔄 إعادة تعيين",
        "save_all_btn": "💾 حفظ جميع النماذج",
        "history_title": "📋 سجلات المحفوظات",
        "search_placeholder": "🔍 بحث (موديل، موسم...)",
        "filter_status": "تصفية الحالة",
        "status_all": "الكل",
        "status_faulty": "معيب",
        "status_correct": "تم التحويل بشكل صحيح",
        "admin_title": "🛠️ إدارة المستخدمين",
        "add_user_title": "إضافة مستخدم جديد",
        "role_select": "الصلاحية",
        "create_user_btn": "إنشاء مستخدم",
        "user_created": "تم إنشاء المستخدم بنجاح.",
        "user_create_err": "حدث خطأ.",
        "delete_user_btn": "حذف المستخدم المحدد",
        "delete_self_err": "لا يمكنك حذف نفسك!",
        "model": "الموديل",
        "season": "الموسم",
        "part": "القطعة",
        "user": "المستخدم",
        "date": "التاريخ",
        "status": "الحالة",
        "faulty_count": "عدد الأخطاء",
        "max_dev": "أقصى انحراف",
        "detail": "التفاصيل",
        "result": "النتيجة"
    }
}

# Firebase başlatma (Secrets kullanarak)
if not firebase_admin._apps:
    try:
        # Secrets verisini al
        key_dict = dict(st.secrets["firebase"])
        
        # Private key içindeki "\n" karakterleri düzelt
        if "private_key" in key_dict:
            key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")

        cred = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.error(f"Firestore bağlantı hatası: {e}. Lütfen Secrets ayarlarını kontrol edin.")

# DB İstemcisi
try:
    db = firestore.client()
except:
    db = None 

# --------------------------------------------------------------------------
# 2. KULLANICI YÖNETİMİ VE GÜVENLİK FONKSİYONLARI
# --------------------------------------------------------------------------

def make_hashes(password):
    """Şifreyi SHA256 ile hashler."""
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    """Girilen şifrenin hash'i ile kayıtlı hash'i karşılaştırır."""
    if make_hashes(password) == hashed_text:
        return hashed_text
    return False

def init_users_db():
    """Eğer veritabanında kullanıcı tablosu yoksa varsayılan admin oluşturur."""
    if db:
        users_ref = db.collection('users')
        # Koleksiyonda en az 1 döküman var mı kontrol et
        docs = users_ref.limit(1).stream()
        if not any(docs):
            # Varsayılan Admin: admin / 1234
            users_ref.document('admin').set({
                'username': 'admin',
                'password': make_hashes('1234'),
                'role': 'admin'
            })

def login_user(username, password):
    """Giriş işlemini kontrol eder."""
    if not db: return None, None
    
    doc_ref = db.collection('users').document(username)
    doc = doc_ref.get()
    
    if doc.exists:
        user_data = doc.to_dict()
        if check_hashes(password, user_data['password']):
            return True, user_data['role']
    return False, None

def create_user(username, password, role):
    """Yeni kullanıcı oluşturur."""
    if not db: return False
    try:
        db.collection('users').document(username).set({
            'username': username,
            'password': make_hashes(password),
            'role': role
        })
        return True
    except:
        return False

def delete_user(username):
    """Kullanıcı siler."""
    if not db: return False
    try:
        db.collection('users').document(username).delete()
        return True
    except:
        return False

def update_password(username, new_password):
    """Kullanıcı şifresini günceller."""
    if not db: return False
    try:
        db.collection('users').document(username).update({
            'password': make_hashes(new_password)
        })
        return True
    except:
        return False

# --------------------------------------------------------------------------
# 3. YARDIMCI PARSER FONKSİYONLARI
# --------------------------------------------------------------------------

def parse_header_info(text):
    if not isinstance(text, str): return None
    clean_text = text.strip()
    prefix_match = re.match(r"^[LM]\d+\/(.*)", clean_text)
    if prefix_match:
        clean_text = prefix_match.group(1)
        
    parts = clean_text.split('-')
    if len(parts) >= 3:
        part_name = parts[-1].strip()
        season = parts[-2].strip()
        model_name = "-".join(parts[:-2]).strip()
        unique_id = f"{model_name}-{season}-{part_name}"
        return {"model": model_name, "season": season, "part": part_name, "unique_id": unique_id, "full_text": text}
    
    pattern = r"(?:L\d+\/)?([\w\-\s]+)-([A-Z0-9]+)-([A-Z0-9]+)"
    match = re.search(pattern, text)
    if match:
        model = match.group(1).strip()
        season = match.group(2).strip()
        part = match.group(3).strip()
        unique_id = f"{model}-{season}-{part}"
        return {"model": model, "season": season, "part": part, "unique_id": unique_id, "full_text": text}
    return None

def clean_number(val):
    try:
        if isinstance(val, (int, float)): return float(val)
        val = str(val).replace(',', '.')
        found = re.findall(r"[-+]?\d*\.\d+|\d+", val)
        if found: return float(found[0])
        return 0.0
    except: return 0.0

def clean_number_excel(val):
    try:
        if isinstance(val, (int, float)): return float(val)
        val = str(val).replace(',', '.')
        found = re.findall(r"[-+]?\d*\.\d+|\d+", val)
        if found: return float(found[0])
        return 0.0
    except: return 0.0

def get_max_abs_value_in_range(row_series, start_idx, end_idx):
    max_val = 0.0
    limit = min(end_idx, len(row_series))
    for idx in range(start_idx, limit):
        val = row_series[idx]
        num = clean_number_excel(val)
        if abs(num) > abs(max_val): max_val = num
    return abs(max_val)

def normalize_header(text):
    return str(text).upper().replace(" ", "").replace("\t", "").strip()

# --------------------------------------------------------------------------
# 4. PARSER MANTIKLARI
# --------------------------------------------------------------------------

def parse_gerber_metadata(text_block):
    if not text_block: return None
    info = parse_header_info(text_block)
    if info:
        return {"model_adi": info['model'], "sezon": info['season'], "parca_adi": info['part']}
    return None

def parse_gerber_table(text, value_type):
    if not text: return pd.DataFrame()
    lines = text.strip().split('\n')
    data = []
    size_pattern = r"^(\*?[A-Z0-9]+)\s+(.*)" 
    for line in lines:
        line = line.strip()
        if not line: continue
        match = re.match(size_pattern, line)
        if match:
            beden = match.group(1).replace("*", "")
            rest = match.group(2)
            columns = [c.strip() for c in rest.split('\t')] if '\t' in rest else re.split(r'\s+', rest)
            try:
                val = 0.0
                numeric_values = []
                for c in columns:
                    try:
                        if c and any(char.isdigit() for char in c): numeric_values.append(clean_number(c))
                    except: pass
                if value_type == 'cevre':
                    if numeric_values: val = max(numeric_values)
                elif value_type == 'en': 
                    if '\t' in rest and len(columns) >= 4: val = clean_number(columns[3]) 
                    else:
                        if len(numeric_values) >= 3:
                            for v in numeric_values[2:]:
                                if abs(v) > 1.0: 
                                    val = v; break
                            if val == 0.0 and len(numeric_values) > 2: val = numeric_values[2]
                elif value_type == 'boy': 
                     if len(numeric_values) > 1: val = numeric_values[1]
                data.append({"Beden": beden, value_type: abs(val)})
            except: continue
    return pd.DataFrame(data)

def parse_polypattern(text):
    if not text: return pd.DataFrame()
    lines = text.strip().split('\n')
    data = []
    for line in lines:
        clean_line = line.replace("*", " ")
        parts = re.split(r'\s+', clean_line.strip())
        if len(parts) >= 4:
            if not parts[0][0].isdigit():
                try:
                    data.append({"Beden": parts[0], "poly_boy": clean_number(parts[1]), "poly_en": clean_number(parts[2]), "poly_cevre": clean_number(parts[3])})
                except: continue
    return pd.DataFrame(data)

def parse_excel_gerber_sheet(df):
    parts_data = {}
    idx = 0
    total_rows = len(df)
    while idx < total_rows:
        row = df.iloc[idx]
        row_str = [str(x).strip() for x in row.tolist()]
        if "Boyut" in row_str:
            indices = [i for i, x in enumerate(row_str) if x == "Boyut"]
            if len(indices) >= 3:
                header_cell = str(df.iloc[idx, indices[0]+1])
                meta = parse_header_info(header_cell)
                if not meta:
                    idx += 1; continue
                
                block_cevre = (indices[0]+1, indices[1])
                block_en = (indices[1]+1, indices[2])
                block_boy = (indices[2]+1, min(indices[2] + 20, len(df.columns)))
                
                col_cevre = -1
                for c in range(block_cevre[0], block_cevre[1]):
                    if "TOPLAM" in normalize_header(df.iloc[idx, c]): col_cevre = c; break
                
                col_en = -1
                for c in range(block_en[0], block_en[1]):
                    if "YMESA" in normalize_header(df.iloc[idx, c]): col_en = c; break
                if col_en == -1:
                    for c in range(block_en[0], block_en[1]):
                        if "TOPLAM" in normalize_header(df.iloc[idx, c]): col_en = c; break

                col_boy = -1
                for c in range(block_boy[0], block_boy[1]):
                    if "XMESA" in normalize_header(df.iloc[idx, c]): col_boy = c; break
                if col_boy == -1:
                    for c in range(block_boy[0], block_boy[1]):
                        if "TOPLAM" in normalize_header(df.iloc[idx, c]): col_boy = c; break
                
                current_row = idx + 1
                part_measurements = []
                while current_row < total_rows:
                    vals = df.iloc[current_row]
                    beden_raw = str(vals[indices[0]]).strip()
                    if not beden_raw or beden_raw == "Boyut" or beden_raw == "nan" or pd.isna(vals[indices[0]]): break
                    beden = beden_raw.replace("*", "").strip()
                    val_cevre = clean_number_excel(vals[col_cevre]) if col_cevre != -1 else 0.0
                    if val_cevre == 0.0: val_cevre = get_max_abs_value_in_range(vals, block_cevre[0], block_cevre[1])
                    val_en = clean_number_excel(vals[col_en]) if col_en != -1 else 0.0
                    if val_en == 0.0: val_en = get_max_abs_value_in_range(vals, block_en[0], block_en[1])
                    val_boy = clean_number_excel(vals[col_boy]) if col_boy != -1 else 0.0
                    if val_boy == 0.0: val_boy = get_max_abs_value_in_range(vals, block_boy[0], block_boy[1])
                    part_measurements.append({"Beden": beden, "cevre": abs(val_cevre), "en": abs(val_en), "boy": abs(val_boy)})
                    current_row += 1
                if part_measurements:
                    parts_data[meta['unique_id']] = {"meta": meta, "df": pd.DataFrame(part_measurements)}
                idx = current_row 
            else: idx += 1
        else: idx += 1
    return parts_data

def parse_excel_pp_sheet(df):
    parts_data = {}
    idx = 0
    total_rows = len(df)
    while idx < total_rows:
        row = df.iloc[idx]
        row_str = [str(x).strip() for x in row.tolist()]
        if "Boy" in row_str and "En" in row_str and "Çevre" in row_str:
            part_header = str(row.iloc[0])
            meta = parse_header_info(part_header)
            if not meta:
                idx += 1; continue
            try:
                col_boy = row_str.index("Boy"); col_en = row_str.index("En"); col_cevre = row_str.index("Çevre")
            except: 
                idx += 1; continue
            current_row = idx + 1
            part_measurements = []
            while current_row < total_rows:
                vals = df.iloc[current_row]
                first_cell = str(vals.iloc[0]).strip()
                if not first_cell or first_cell == "nan" or "Boy" in str(vals.values):
                    if "Boy" in str(vals.values): break 
                    if not first_cell or first_cell == "nan": current_row += 1; continue
                beden = first_cell.replace("*", "").strip()
                p_boy = clean_number_excel(vals.iloc[col_boy])
                p_en = clean_number_excel(vals.iloc[col_en])
                p_cevre = clean_number_excel(vals.iloc[col_cevre])
                part_measurements.append({"Beden": beden, "poly_boy": p_boy, "poly_en": p_en, "poly_cevre": p_cevre})
                current_row += 1
            if part_measurements:
                parts_data[meta['unique_id']] = {"meta": meta, "df": pd.DataFrame(part_measurements)}
            idx = current_row
        else: idx += 1
    return parts_data

# --------------------------------------------------------------------------
# 5. SAYFA DÜZENİ VE AKIŞ
# --------------------------------------------------------------------------

def main():
    # Session State Başlangıç Değerleri
    if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
    if 'username' not in st.session_state: st.session_state['username'] = ""
    if 'role' not in st.session_state: st.session_state['role'] = ""
    if 'language' not in st.session_state: st.session_state['language'] = "TR"
    
    if 'current_model' not in st.session_state: st.session_state['current_model'] = {}
    if 'model_parts' not in st.session_state: st.session_state['model_parts'] = [] 
    if 'excel_results' not in st.session_state: st.session_state['excel_results'] = {} 
    if 'uploader_key' not in st.session_state: st.session_state['uploader_key'] = 0

    # DB başlat ve varsayılan kullanıcı kontrolü
    init_users_db()

    # Dil Kısayolu
    t = TRANSLATIONS[st.session_state['language']]

    # DİL SEÇİM BUTONLARI (ÜST KISIM)
    # Üst kısımda sağa hizalı kolonlar
    top_c1, top_c2, top_c3, top_c4 = st.columns([10, 1, 1, 1])
    with top_c2:
        if st.button("TR"): st.session_state['language'] = "TR"; st.rerun()
    with top_c3:
        if st.button("ENG"): st.session_state['language'] = "ENG"; st.rerun()
    with top_c4:
        if st.button("ARB"): st.session_state['language'] = "ARB"; st.rerun()

    # --- GİRİŞ EKRANI ---
    if not st.session_state['logged_in']:
        st.title(t["app_title"])
        st.header(t["login_title"])
        
        col1, col2, col3 = st.columns([1,2,1])
        with col2:
            with st.form("login_form"):
                username_input = st.text_input(t["username"])
                password_input = st.text_input(t["password"], type="password")
                submit_login = st.form_submit_button(t["login_btn"])
                
                if submit_login:
                    is_valid, role = login_user(username_input, password_input)
                    if is_valid:
                        st.session_state['logged_in'] = True
                        st.session_state['username'] = username_input
                        st.session_state['role'] = role
                        st.success(t["login_success"])
                        st.rerun()
                    else:
                        st.error(t["login_error"])
        return

    # --- ANA UYGULAMA (GİRİŞ YAPILDIKTAN SONRA) ---
    st.sidebar.image("https://cdn-icons-png.flaticon.com/512/3022/3022329.png", width=100)
    st.sidebar.write(f"👤 **{st.session_state['username']}** ({st.session_state['role']})")
    
    if st.sidebar.button(t["logout"]):
        st.session_state['logged_in'] = False
        st.session_state['username'] = ""
        st.session_state['role'] = ""
        st.rerun()

    # --- ŞİFRE DEĞİŞTİRME ---
    with st.sidebar.expander(t["change_pass_title"]):
        with st.form("change_password_form"):
            current_pass = st.text_input(t["curr_pass"], type="password")
            new_pass = st.text_input(t["new_pass"], type="password")
            confirm_pass = st.text_input(t["confirm_pass"], type="password")
            submit_pass = st.form_submit_button(t["update_pass_btn"])
            
            if submit_pass:
                is_valid, _ = login_user(st.session_state['username'], current_pass)
                if not is_valid:
                    st.error(t["pass_current_err"])
                elif new_pass != confirm_pass:
                    st.error(t["pass_mismatch"])
                elif not new_pass:
                    st.error("!")
                else:
                    if update_password(st.session_state['username'], new_pass):
                        st.success(t["pass_update_success"])
                    else:
                        st.error(t["pass_update_error"])

    # Menü Seçenekleri (Dil Çevirisine Göre Eşleme)
    # Anahtar bazlı çalışmak daha güvenli
    menu_keys = ["menu_manual", "menu_excel", "menu_history"]
    if st.session_state['role'] == 'admin':
        menu_keys.append("menu_admin")
    
    # Görünen isimler
    menu_labels = [t[k] for k in menu_keys]
    
    # Sidebar Başlık
    st.sidebar.header(t["menu_header"])
    selected_label = st.sidebar.radio("Navigation", menu_labels, label_visibility="collapsed")
    
    # Seçilen label'ın hangi key'e denk geldiğini bul
    selected_key = menu_keys[menu_labels.index(selected_label)]

    if selected_key == "menu_manual":
        new_control_page(t)
    elif selected_key == "menu_excel":
        excel_control_page(t)
    elif selected_key == "menu_history":
        history_page(t)
    elif selected_key == "menu_admin":
        admin_users_page(t)

def admin_users_page(t):
    st.header(t["admin_title"])
    
    with st.expander(t["add_user_title"]):
        with st.form("add_user_form"):
            new_user = st.text_input(t["username"])
            new_pass = st.text_input(t["password"], type="password")
            new_role = st.selectbox(t["role_select"], ["user", "admin"])
            submitted = st.form_submit_button(t["create_user_btn"])
            
            if submitted:
                if create_user(new_user, new_pass, new_role):
                    st.success(t["user_created"])
                else:
                    st.error(t["user_create_err"])

    st.divider()
    
    if db:
        users = db.collection('users').stream()
        user_list = []
        for u in users:
            user_list.append(u.to_dict())
        
        if user_list:
            df_users = pd.DataFrame(user_list)
            st.dataframe(df_users[['username', 'role']], use_container_width=True)
            
            user_to_delete = st.selectbox(t["delete_user_btn"], df_users['username'].unique())
            if st.button("Sil / Delete"):
                if user_to_delete == 'admin' or user_to_delete == st.session_state['username']:
                    st.error(t["delete_self_err"])
                else:
                    delete_user(user_to_delete)
                    st.success(f"{user_to_delete} deleted!")
                    st.rerun()

def excel_control_page(t):
    st.header(t["excel_title"])
    st.info(t["excel_info"])

    col1, col2 = st.columns(2)
    with col1:
        business_unit = st.selectbox(t["bu_select"], ["BU1", "BU3", "BU5"], key="excel_bu")
    
    uploaded_file = st.file_uploader(t["upload_label"], type=["xlsx"], key=f"uploader_{st.session_state['uploader_key']}")

    if uploaded_file:
        if st.button(t["analyze_file_btn"], type="primary"):
            with st.spinner("..."):
                try:
                    xls = pd.read_excel(uploaded_file, sheet_name=None, header=None)
                    sheet_names = list(xls.keys())
                    all_gerber_parts = {}
                    all_pp_parts = {}
                    for sheet in sheet_names:
                        sheet_upper = sheet.upper()
                        df_sheet = xls[sheet]
                        if "GERBER" in sheet_upper:
                            all_gerber_parts.update(parse_excel_gerber_sheet(df_sheet))
                        elif "PP" in sheet_upper or "POLY" in sheet_upper:
                            all_pp_parts.update(parse_excel_pp_sheet(df_sheet))
                    
                    if not all_gerber_parts: st.error("Gerber?"); return
                    if not all_pp_parts: st.error("Polypattern?"); return
                    
                    grouped_results = {}
                    for unique_id, pp_data in all_pp_parts.items():
                        if unique_id in all_gerber_parts:
                            gerber_data = all_gerber_parts[unique_id]
                            df_g = gerber_data['df']; df_p = pp_data['df']; meta = pp_data['meta']
                            try:
                                df_final = df_g.merge(df_p, on="Beden", how="inner")
                                df_final['Fark_Boy'] = df_final['boy'] - df_final['poly_boy']
                                df_final['Fark_En'] = df_final['en'] - df_final['poly_en']
                                df_final['Fark_Cevre'] = df_final['cevre'] - df_final['poly_cevre']
                                model_key = f"{meta['model']} ({meta['season']})"
                                if model_key not in grouped_results:
                                    grouped_results[model_key] = {"model": meta['model'], "season": meta['season'], "parts": []}
                                grouped_results[model_key]["parts"].append({"parca_adi": meta['part'], "df": df_final})
                            except: pass
                    
                    st.session_state['excel_results'] = grouped_results
                    st.success("OK")
                except Exception as e: st.error(f"{t['error_parse']}: {e}")

    if st.session_state.get('excel_results'):
        results = st.session_state['excel_results']
        st.divider(); st.subheader(t["result"])
        for model_key, model_data in results.items():
            with st.container():
                st.info(f"📌 {t['model']}: {model_key} | {t['slot_count']}: {len(model_data['parts'])}")
                parts_list_for_save = []
                has_fault = False
                for part in model_data['parts']:
                    df = part['df']; parca_adi = part['parca_adi']; tolerans = 0.05
                    hatali_satirlar = df[(df['Fark_Boy'].abs()>tolerans)|(df['Fark_En'].abs()>tolerans)|(df['Fark_Cevre'].abs()>tolerans)]
                    hata_var = not hatali_satirlar.empty
                    if hata_var: has_fault = True
                    with st.expander(f"{'⚠️' if hata_var else '✅'} {parca_adi}", expanded=hata_var):
                        cols = ['boy','poly_boy','en','poly_en','cevre','poly_cevre','Fark_Boy','Fark_En','Fark_Cevre']
                        ex_cols = [c for c in cols if c in df.columns]
                        st.dataframe(df.style.format("{:.2f}", subset=ex_cols).map(lambda x: 'background-color:#ffcccc' if isinstance(x,(int,float)) and abs(x)>tolerans else '', subset=['Fark_Boy','Fark_En','Fark_Cevre']), use_container_width=True)
                    parts_list_for_save.append({"parca_adi": parca_adi, "durum": "Hatalı" if hata_var else "Doğru", "hata_detayi": hatali_satirlar[['Beden','Fark_Boy','Fark_En','Fark_Cevre']].to_dict('records') if hata_var else [], "timestamp": datetime.now()})
                model_data['save_ready'] = {"genel_durum": "Hatalı" if has_fault else "Doğru Çevrilmiş", "parts_list": parts_list_for_save}
                st.markdown("---")

        c_save, c_reset = st.columns([3, 1])
        with c_save:
            if st.button(t["save_all_btn"], type="primary", use_container_width=True):
                if not db: return
                batch = db.batch(); cnt = 0
                for mk, data in results.items():
                    sinfo = data['save_ready']
                    doc_ref = db.collection('qc_records').document()
                    doc_data = {
                        'kullanici': st.session_state['username'],
                        'tarih': datetime.now(),
                        'business_unit': business_unit,
                        'model_adi': data['model'],
                        'sezon': data['season'],
                        'parca_sayisi': len(sinfo['parts_list']),
                        'genel_durum': sinfo['genel_durum'],
                        'parca_detaylari': sinfo['parts_list']
                    }
                    batch.set(doc_ref, doc_data); cnt += 1
                batch.commit(); st.balloons(); st.success(t["save_success"]); st.session_state['excel_results']={}; st.session_state['uploader_key']+=1; st.rerun()
        with c_reset:
            if st.button(t["reset_btn"], use_container_width=True): st.session_state['excel_results']={}; st.session_state['uploader_key']+=1; st.rerun()

def new_control_page(t):
    st.header(t["menu_manual"])
    with st.expander(t["detail"], expanded=True):
        c1, c2 = st.columns(2)
        with c1: business_unit = st.selectbox(t["bu_select"], ["BU1", "BU3", "BU5"])
        with c2: slot_count = st.number_input(t["slot_count"], 1, 5, 1)
    
    st.divider(); tabs = st.tabs([f"{t['part']} {i+1}" for i in range(slot_count)]); inputs = {}
    for i, tab in enumerate(tabs):
        with tab:
            c1, c2 = st.columns(2)
            with c1: st.subheader(t["gerber_header"]); inputs[f"g_c_{i}"]=st.text_area("Çevre/Circumference/المحيط",key=f"g_c{i}",height=100); inputs[f"g_e_{i}"]=st.text_area("En/Width/العرض",key=f"g_e{i}",height=100); inputs[f"g_b_{i}"]=st.text_area("Boy/Length/الطول",key=f"g_b{i}",height=100)
            with c2: st.subheader(t["pp_header"]); inputs[f"poly_{i}"]=st.text_area("Data",key=f"p{i}",height=340)

    st.markdown("---")
    if st.button(t["analyze_btn"], type="primary", use_container_width=True):
        st.session_state['analysis_results'] = {}
        for i in range(slot_count):
            gc=inputs[f"g_c_{i}"]; ge=inputs[f"g_e_{i}"]; gb=inputs[f"g_b_{i}"]; pp=inputs[f"poly_{i}"]
            if not (gc and ge and gb and pp): continue
            if 'active_session' not in st.session_state:
                meta = parse_gerber_metadata(gc)
                if meta: st.session_state['active_session']=True; st.session_state['current_model']={"model_adi":meta['model_adi'],"sezon":meta['sezon'],"bu":business_unit}
            lmeta = parse_gerber_metadata(gc); pname = lmeta['parca_adi'] if lmeta else f"{t['part']} {i+1}"
            dfc = parse_gerber_table(gc,'cevre'); dfe = parse_gerber_table(ge,'en'); dfb = parse_gerber_table(gb,'boy'); dfp = parse_polypattern(pp)
            if not dfc.empty and not dfe.empty and not dfb.empty and not dfp.empty:
                try:
                    dft = dfc.merge(dfe, on="Beden").merge(dfb, on="Beden"); dff = dft.merge(dfp, on="Beden")
                    dff['Fark_Boy']=dff['boy']-dff['poly_boy']; dff['Fark_En']=dff['en']-dff['poly_en']; dff['Fark_Cevre']=dff['cevre']-dff['poly_cevre']
                    st.session_state['analysis_results'][i]={"df":dff, "parca_adi":pname, "saved":False}
                except: st.error(f"{t['part']} {i+1} Err")

    if st.session_state.get('analysis_results'):
        for i, res in st.session_state['analysis_results'].items():
            if res['saved']: continue
            with st.expander(f"{t['result']}: {res['parca_adi']}", expanded=True):
                st.dataframe(res['df'])
                if st.button(f"{t['save_list_btn']} {i}", key=f"b_{i}"):
                    st.session_state['model_parts'].append({"parca_adi":res['parca_adi'], "durum":"Doğru", "timestamp":datetime.now()}) 
                    st.session_state['analysis_results'][i]['saved']=True; st.rerun()

    if st.session_state.get('active_session') and st.session_state['model_parts']:
        if st.button(t["finish_save_btn"]): 
            save_to_firestore(st.session_state['username'], business_unit, t)

def save_to_firestore(user, bu, t):
    if not db: return
    mdata = st.session_state['current_model']; parts = st.session_state['model_parts']
    genel = "Doğru Çevrilmiş"
    db.collection('qc_records').add({
        'kullanici': user, 'tarih': datetime.now(), 'business_unit': bu,
        'model_adi': mdata.get('model_adi'), 'sezon': mdata.get('sezon'),
        'parca_sayisi': len(parts), 'genel_durum': genel, 'parca_detaylari': parts
    })
    st.success(t["save_success"]); st.session_state['model_parts']=[]; st.session_state['current_model']={}; st.session_state['analysis_results']={}; del st.session_state['active_session']; st.rerun()

def history_page(t):
    st.header(t["history_title"])
    if not db: st.warning("DB Yok"); return
    
    c1, c2 = st.columns(2)
    term = c1.text_input(t["search_placeholder"])
    status = c2.selectbox(t["filter_status"], [t["status_all"], t["status_faulty"], t["status_correct"]])
    
    query = db.collection('qc_records')
    if st.session_state['role'] != 'admin':
        query = query.where('kullanici', '==', st.session_state['username'])
    
    query = query.order_by('tarih', direction=firestore.Query.DESCENDING).limit(100)
    docs = query.stream()
    
    data = []
    for doc in docs:
        d = doc.to_dict(); d['id'] = doc.id
        parts = d.get('parca_detaylari', [])
        faults = [p for p in parts if p.get('durum') == 'Hatalı']
        d['hatali_sayi'] = len(faults)
        max_dev = 0.0; summaries = []
        for p in faults:
            p_errs = []
            for det in p.get('hata_detayi', []):
                fb=det.get('Fark_Boy',0); fe=det.get('Fark_En',0); fc=det.get('Fark_Cevre',0)
                curr_max = max(abs(fb), abs(fe), abs(fc))
                if curr_max > max_dev: max_dev = curr_max
                errs = []
                if abs(fb)>0.05: errs.append(f"Boy:{fb:.2f}")
                if abs(fe)>0.05: errs.append(f"En:{fe:.2f}")
                if abs(fc)>0.05: errs.append(f"Çv:{fc:.2f}")
                if errs: p_errs.append(f"{det.get('Beden','?')}[{','.join(errs)}]")
            if p_errs: summaries.append(f"{p.get('parca_adi')}: {' '.join(p_errs)}")
        d['hata_ozeti'] = " | ".join(summaries)
        d['max_sapma'] = max_dev
        d['tarih_str'] = pd.to_datetime(d['tarih']).strftime('%Y-%m-%d %H:%M') if d.get('tarih') else "-"
        data.append(d)

    if not data: st.info("..."); return
    df = pd.DataFrame(data)
    
    if term:
        t_term = term.lower()
        df = df[df['model_adi'].str.lower().str.contains(t_term, na=False) | df['sezon'].str.lower().str.contains(t_term, na=False)]
    
    # Durum filtresi çeviriye duyarlı hale getirilmeli
    # DB'de kayıtlı değerler: "Hatalı", "Doğru Çevrilmiş" (Bunlar hardcoded kalıyor logic değişmesin diye)
    # Filtre seçimleri ise translate edilmiş.
    
    db_status_map = {t["status_faulty"]: "Hatalı", t["status_correct"]: "Doğru Çevrilmiş"}
    
    if status != t["status_all"]:
        db_val = db_status_map.get(status, status)
        df = df[df['genel_durum'] == db_val]
        
    disp_cols = {'tarih_str':t["date"], 'kullanici':t["user"], 'business_unit':'BU', 'model_adi':t["model"], 'sezon':t["season"], 'genel_durum':t["status"], 'hatali_sayi':t["faulty_count"], 'max_sapma':t["max_dev"], 'hata_ozeti':t["detail"]}
    used_cols = [c for c in disp_cols.keys() if c in df.columns]
    
    st.dataframe(df[used_cols].rename(columns=disp_cols).style.applymap(lambda x: 'color:red;font-weight:bold' if x=='Hatalı' else ('color:green;font-weight:bold' if x=='Doğru Çevrilmiş' else ''), subset=[t['status']]), use_container_width=True)
    
    st.markdown("---"); st.subheader(f"🔍 {t['detail']}")
    opts = df.apply(lambda x: f"{x['model_adi']} ({x['sezon']}) - {x['tarih_str']}", axis=1).tolist()
    sel = st.selectbox("Select", opts, label_visibility="collapsed")
    if sel:
        row = df.iloc[opts.index(sel)]
        c1,c2,c3 = st.columns(3); c1.info(f"{t['model']}: {row['model_adi']}"); c2.info(f"{t['user']}: {row['kullanici']}"); c3.info(f"{t['date']}: {row['tarih_str']}")
        for p in row.get('parca_detaylari', []):
            with st.expander(f"{'⚠️' if p['durum']=='Hatalı' else '✅'} {p['parca_adi']}"):
                if p['durum']=='Hatalı': st.dataframe(pd.DataFrame(p.get('hata_detayi',[])))
                else: st.success("OK")

if __name__ == "__main__":
    main()
