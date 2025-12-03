import streamlit as st
import pandas as pd
import re
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import io

# --------------------------------------------------------------------------
# 1. AYARLAR VE FIREBASE BAĞLANTISI
# --------------------------------------------------------------------------
st.set_page_config(page_title="Gerber vs Polypattern Kontrol", layout="wide")

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
# 2. YARDIMCI FONKSİYONLAR
# --------------------------------------------------------------------------

def parse_header_info(text):
    """
    Header metninden Model, Sezon ve Parça bilgilerini ayrıştırır.
    Örn: L1/UTJW-DW0DW22280-SP26-OBAS -> {model: UTJW..., season: SP26, part: OBAS, unique_id: ...}
    """
    if not isinstance(text, str): return None
    
    # Regex Güncellemesi: Daha esnek hale getirildi
    # (Opsiyonel L1/...) (Model: Boşluk içerebilir) - (Sezon) - (Parça)
    pattern = r"(?:L\d+\/)?([\w\-\s]+)-([A-Z]{2}\d{2})-([A-Z0-9]+)"
    match = re.search(pattern, text)
    
    if match:
        model = match.group(1).strip()
        season = match.group(2).strip()
        part = match.group(3).strip()
        # Benzersiz kimlik: Model-Sezon-Parça
        unique_id = f"{model}-{season}-{part}"
        return {
            "model": model,
            "season": season,
            "part": part,
            "unique_id": unique_id,
            "full_text": text
        }
    return None

def clean_number(val):
    """Metni floata çevirir."""
    try:
        if isinstance(val, (int, float)):
            return float(val)
        val = str(val).replace(',', '.')
        found = re.findall(r"[-+]?\d*\.\d+|\d+", val)
        if found:
            return float(found[0])
        return 0.0
    except:
        return 0.0

def clean_number_excel(val):
    """Excel hücre değerini temizleyip float'a çevirir."""
    try:
        if isinstance(val, (int, float)):
            return float(val)
        val = str(val).replace(',', '.')
        found = re.findall(r"[-+]?\d*\.\d+|\d+", val)
        if found:
            return float(found[0])
        return 0.0
    except:
        return 0.0

def get_max_abs_value_in_range(row_series, start_idx, end_idx):
    """Belirtilen aralıktaki en büyük mutlak sayısal değeri bulur."""
    max_val = 0.0
    limit = min(end_idx, len(row_series))
    for idx in range(start_idx, limit):
        val = row_series[idx]
        num = clean_number_excel(val)
        if abs(num) > abs(max_val):
            max_val = num
    return abs(max_val)

# --------------------------------------------------------------------------
# 3. MANUEL GİRİŞ PARSERLARI
# --------------------------------------------------------------------------

def parse_gerber_metadata(text_block):
    """Manuel giriş için metadan bilgi çeker."""
    if not text_block: return None
    info = parse_header_info(text_block)
    if info:
        return {
            "model_adi": info['model'],
            "sezon": info['season'],
            "parca_adi": info['part']
        }
    return None

def parse_gerber_table(text, value_type):
    """Manuel giriş Gerber parser."""
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
            if '\t' in rest:
                columns = [c.strip() for c in rest.split('\t')] 
            else:
                columns = re.split(r'\s+', rest)

            try:
                val = 0.0
                numeric_values = []
                for c in columns:
                    try:
                        if c and any(char.isdigit() for char in c):
                            numeric_values.append(clean_number(c))
                    except: pass

                if value_type == 'cevre':
                    if numeric_values: val = max(numeric_values)
                elif value_type == 'en': 
                    # Manuel girişte Y Mesafe (En) genelde 3. sütun civarı veya büyük değer
                    if '\t' in rest and len(columns) >= 4: val = clean_number(columns[3]) 
                    else:
                        if len(numeric_values) >= 3:
                            for v in numeric_values[2:]:
                                if abs(v) > 1.0: 
                                    val = v
                                    break
                            if val == 0.0 and len(numeric_values) > 2: val = numeric_values[2]
                elif value_type == 'boy': 
                     if len(numeric_values) > 1: val = numeric_values[1]

                data.append({"Beden": beden, value_type: abs(val)})
            except: continue
    return pd.DataFrame(data)

def parse_polypattern(text):
    """Manuel giriş Polypattern parser."""
    if not text: return pd.DataFrame()
    lines = text.strip().split('\n')
    data = []
    for line in lines:
        clean_line = line.replace("*", " ")
        parts = re.split(r'\s+', clean_line.strip())
        if len(parts) >= 4:
            if not parts[0][0].isdigit():
                try:
                    data.append({
                        "Beden": parts[0],
                        "poly_boy": clean_number(parts[1]),
                        "poly_en": clean_number(parts[2]),
                        "poly_cevre": clean_number(parts[3])
                    })
                except: continue
    return pd.DataFrame(data)

# --------------------------------------------------------------------------
# 4. EXCEL PARSER FONKSİYONLARI (OTOMATİK ÇOKLU MODEL)
# --------------------------------------------------------------------------

def parse_excel_gerber_sheet(df):
    """
    Gerber sayfasını tarar. Birden fazla parça/model olabilir.
    Her bulunan parçayı 'unique_id' (Model-Sezon-Parça) ile sözlüğe ekler.
    """
    parts_data = {}
    
    for idx, row in df.iterrows():
        # DÜZELTME: Hücre değerlerini string'e çevirip boşlukları temizliyoruz.
        # Böylece "Boyut " gibi hatalı yazımlar da "Boyut" olarak algılanır.
        row_str = [str(x).strip() for x in row.tolist()]
        
        if "Boyut" in row_str:
            indices = [i for i, x in enumerate(row_str) if x == "Boyut"]
            
            if len(indices) >= 3:
                header_cell = str(df.iloc[idx, indices[0]+1])
                meta = parse_header_info(header_cell)
                
                if not meta:
                    continue
                
                # Sütun İndeksleri
                # 1. ÇEVRE (Blok 1): 'Toplam' ara
                col_cevre = -1
                for c in range(indices[0], indices[1]):
                    if "Toplam" in str(df.iloc[idx, c]):
                        col_cevre = c
                        break
                        
                # 2. EN (Blok 2): 'Y Mesafe' ara
                col_en = -1
                for c in range(indices[1], indices[2]):
                    if "Y Mesafe" in str(df.iloc[idx, c]):
                        col_en = c
                        break
                        
                # 3. BOY (Blok 3): 'X Mesafe' ara
                col_boy = -1
                limit = min(indices[2] + 20, len(df.columns))
                for c in range(indices[2], limit):
                    if "X Mesafe" in str(df.iloc[idx, c]):
                        col_boy = c
                        break
                
                current_row = idx + 1
                part_measurements = []
                
                while current_row < len(df):
                    vals = df.iloc[current_row]
                    # Beden hücresini de temizleyerek alıyoruz
                    beden_raw = str(vals[indices[0]]).strip()
                    
                    # Döngü bitirme koşulları
                    if not beden_raw or beden_raw == "Boyut" or beden_raw == "nan" or pd.isna(vals[indices[0]]):
                        break
                        
                    beden = beden_raw.replace("*", "").strip()
                    
                    val_cevre = 0.0
                    if col_cevre != -1: val_cevre = clean_number_excel(vals[col_cevre])

                    val_en = 0.0
                    if col_en != -1: val_en = clean_number_excel(vals[col_en])
                        
                    val_boy = 0.0
                    if col_boy != -1: val_boy = clean_number_excel(vals[col_boy])

                    part_measurements.append({
                        "Beden": beden,
                        "cevre": abs(val_cevre),
                        "en": abs(val_en),
                        "boy": abs(val_boy)
                    })
                    current_row += 1
                
                if part_measurements:
                    parts_data[meta['unique_id']] = {
                        "meta": meta,
                        "df": pd.DataFrame(part_measurements)
                    }

    return parts_data

def parse_excel_pp_sheet(df):
    """
    Polypattern sayfasını tarar. Birden fazla parça/model olabilir.
    """
    parts_data = {}
    
    for idx, row in df.iterrows():
        row_str = [str(x).strip() for x in row.tolist()]
        
        if "Boy" in row_str and "En" in row_str and "Çevre" in row_str:
            part_header = str(row.iloc[0])
            meta = parse_header_info(part_header)
            
            if not meta:
                continue
            
            try:
                col_boy = row_str.index("Boy")
                col_en = row_str.index("En")
                col_cevre = row_str.index("Çevre")
            except: continue
                
            current_row = idx + 1
            part_measurements = []
            
            while current_row < len(df):
                vals = df.iloc[current_row]
                first_cell = str(vals.iloc[0]).strip()
                
                if not first_cell or first_cell == "nan" or "Boy" in str(vals.values):
                    if "Boy" in str(vals.values): break
                    if not first_cell or first_cell == "nan":
                        current_row += 1
                        continue
                
                if first_cell and not first_cell[0].isdigit():
                    beden = first_cell.replace("*", "").strip()
                    p_boy = clean_number_excel(vals.iloc[col_boy])
                    p_en = clean_number_excel(vals.iloc[col_en])
                    p_cevre = clean_number_excel(vals.iloc[col_cevre])
                    
                    part_measurements.append({
                        "Beden": beden,
                        "poly_boy": p_boy,
                        "poly_en": p_en,
                        "poly_cevre": p_cevre
                    })
                current_row += 1
            
            if part_measurements:
                parts_data[meta['unique_id']] = {
                    "meta": meta,
                    "df": pd.DataFrame(part_measurements)
                }
                
    return parts_data

# --------------------------------------------------------------------------
# 5. SAYFA DÜZENİ VE AKIŞ
# --------------------------------------------------------------------------

def main():
    if 'current_model' not in st.session_state:
        st.session_state['current_model'] = {}
    if 'model_parts' not in st.session_state:
        st.session_state['model_parts'] = [] 
    if 'excel_results' not in st.session_state:
        st.session_state['excel_results'] = {} 
    if 'uploader_key' not in st.session_state:
        st.session_state['uploader_key'] = 0

    st.title("🏭 Kalıp Ölçü Kontrol Sistemi")
    st.sidebar.image("https://cdn-icons-png.flaticon.com/512/3022/3022329.png", width=100)
    
    user = st.sidebar.text_input("Kullanıcı Adı", "muhendis_user")
    menu = st.sidebar.radio("Menü", ["Yeni Ölçü Kontrolü (Manuel)", "Excel ile Otomatik Kontrol", "Kontrol Listesi / Geçmiş"])

    if menu == "Yeni Ölçü Kontrolü (Manuel)":
        new_control_page(user)
    elif menu == "Excel ile Otomatik Kontrol":
        excel_control_page(user)
    elif menu == "Kontrol Listesi / Geçmiş":
        history_page()

def excel_control_page(user):
    st.header("📂 Excel ile Çoklu Model Kontrolü")
    st.info("Dosya içerisinde istediğiniz kadar Gerber ve Polypattern sayfası (Gerber1, PP1, Gerber2...) bulunabilir. Sistem hepsini tarayıp modelleri otomatik eşleştirir.")

    col1, col2 = st.columns(2)
    with col1:
        business_unit = st.selectbox("Business Unit (BU) Seçiniz", ["BU1", "BU3", "BU5"], key="excel_bu")
    
    uploaded_file = st.file_uploader("Excel Dosyasını Yükleyin (.xlsx)", type=["xlsx"], key=f"uploader_{st.session_state['uploader_key']}")

    if uploaded_file:
        if st.button("🚀 Dosyayı Analiz Et", type="primary"):
            with st.spinner("Dosya taranıyor ve modeller ayrıştırılıyor..."):
                try:
                    xls = pd.read_excel(uploaded_file, sheet_name=None, header=None)
                    sheet_names = list(xls.keys())
                    
                    all_gerber_parts = {}
                    all_pp_parts = {}
                    
                    # 1. Tüm Sayfaları Tara
                    for sheet in sheet_names:
                        sheet_upper = sheet.upper()
                        df_sheet = xls[sheet]
                        
                        if "GERBER" in sheet_upper:
                            g_parts = parse_excel_gerber_sheet(df_sheet)
                            all_gerber_parts.update(g_parts)
                        elif "PP" in sheet_upper or "POLY" in sheet_upper:
                            p_parts = parse_excel_pp_sheet(df_sheet)
                            all_pp_parts.update(p_parts)
                    
                    if not all_gerber_parts:
                        st.error("Hiçbir Gerber verisi bulunamadı. Sayfa isimlerinde 'GERBER' geçtiğinden emin olun.")
                    if not all_pp_parts:
                        st.error("Hiçbir Polypattern verisi bulunamadı. Sayfa isimlerinde 'PP' veya 'POLY' geçtiğinden emin olun.")
                    
                    # 2. Eşleştirme ve Analiz
                    # Sonuçları Model bazında gruplayacağız: { "ModelAdi-Sezon": [Parça1, Parça2...] }
                    grouped_results = {}
                    
                    for unique_id, pp_data in all_pp_parts.items():
                        if unique_id in all_gerber_parts:
                            gerber_data = all_gerber_parts[unique_id]
                            
                            df_g = gerber_data['df']
                            df_p = pp_data['df']
                            meta = pp_data['meta']
                            
                            try:
                                df_final = df_g.merge(df_p, on="Beden", how="inner")
                                df_final['Fark_Boy'] = (df_final['boy'] - df_final['poly_boy']).abs()
                                df_final['Fark_En'] = (df_final['en'] - df_final['poly_en']).abs()
                                df_final['Fark_Cevre'] = (df_final['cevre'] - df_final['poly_cevre']).abs()
                                
                                # Model Grubu Anahtarı
                                model_key = f"{meta['model']} ({meta['season']})"
                                if model_key not in grouped_results:
                                    grouped_results[model_key] = {
                                        "model": meta['model'],
                                        "season": meta['season'],
                                        "parts": []
                                    }
                                
                                grouped_results[model_key]["parts"].append({
                                    "parca_adi": meta['part'],
                                    "df": df_final
                                })
                                
                            except Exception as e:
                                st.warning(f"{unique_id} birleştirilirken hata: {e}")
                        else:
                            st.warning(f"⚠️ {unique_id} Polypattern'de var ama Gerber'de bulunamadı.")
                    
                    st.session_state['excel_results'] = grouped_results
                    st.success(f"İşlem Tamam! Toplam {len(grouped_results)} farklı model bulundu.")
                    
                except Exception as e:
                    st.error(f"Hata oluştu: {e}")

    # --- SONUÇLARI GÖSTER VE KAYDET ---
    if st.session_state.get('excel_results'):
        results = st.session_state['excel_results']
        
        st.divider()
        st.subheader("📊 Analiz Sonuçları (Model Bazlı)")
        
        # Her Model İçin Ayrı Bir Kart
        for model_key, model_data in results.items():
            with st.container():
                st.info(f"📌 **Model:** {model_key} | **Parça Sayısı:** {len(model_data['parts'])}")
                
                parts_list_for_save = []
                has_fault_in_model = False
                
                for part in model_data['parts']:
                    df = part['df']
                    parca_adi = part['parca_adi']
                    
                    tolerans = 0.05
                    hatali_satirlar = df[
                        (df['Fark_Boy'] > tolerans) | 
                        (df['Fark_En'] > tolerans) | 
                        (df['Fark_Cevre'] > tolerans)
                    ]
                    hata_var = not hatali_satirlar.empty
                    if hata_var: has_fault_in_model = True
                    
                    emoji = "⚠️" if hata_var else "✅"
                    with st.expander(f"{emoji} {parca_adi}", expanded=hata_var):
                        numeric_cols = ['boy', 'poly_boy', 'en', 'poly_en', 'cevre', 'poly_cevre', 'Fark_Boy', 'Fark_En', 'Fark_Cevre']
                        existing_cols = [c for c in numeric_cols if c in df.columns]
                        st.dataframe(
                            df.style.format("{:.2f}", subset=existing_cols).map(
                                lambda x: 'background-color: #ffcccc' if isinstance(x, (int, float)) and abs(x) > tolerans else '',
                                subset=['Fark_Boy', 'Fark_En', 'Fark_Cevre']
                            ), use_container_width=True
                        )
                        if hata_var: st.error("Fark tespit edildi.")
                    
                    # Kayıt formatı hazırla
                    parts_list_for_save.append({
                        "parca_adi": parca_adi,
                        "durum": "Hatalı" if hata_var else "Doğru",
                        "hata_detayi": hatali_satirlar[['Beden', 'Fark_Boy', 'Fark_En', 'Fark_Cevre']].to_dict('records') if hata_var else [],
                        "timestamp": datetime.now()
                    })
                
                # Her modelin kendi verisini Session State'e kaydedelim ki Save butonuna basınca hazır olsun
                model_data['save_ready'] = {
                    "genel_durum": "Hatalı" if has_fault_in_model else "Doğru Çevrilmiş",
                    "parts_list": parts_list_for_save
                }
                st.markdown("---")

        # --- AKSİYON BUTONLARI ---
        col_save, col_reset = st.columns([3, 1])
        
        with col_save:
            if st.button("💾 Tüm Modelleri Kaydet", type="primary", use_container_width=True):
                if not db:
                    st.warning("Veritabanı bağlantısı yok.")
                else:
                    saved_count = 0
                    batch = db.batch() # Batch write ile daha hızlı ve güvenli
                    
                    for model_key, data in results.items():
                        save_info = data['save_ready']
                        doc_ref = db.collection('qc_records').document()
                        
                        doc_data = {
                            'kullanici': user,
                            'tarih': datetime.now(),
                            'business_unit': business_unit,
                            'model_adi': data['model'],
                            'sezon': data['season'],
                            'parca_sayisi': len(save_info['parts_list']),
                            'genel_durum': save_info['genel_durum'],
                            'parca_detaylari': save_info['parts_list']
                        }
                        batch.set(doc_ref, doc_data)
                        saved_count += 1
                    
                    batch.commit()
                    st.balloons()
                    st.success(f"{saved_count} adet model başarıyla veritabanına kaydedildi!")
                    
                    # Sıfırla
                    st.session_state['excel_results'] = {}
                    st.session_state['uploader_key'] += 1
                    st.rerun()

        with col_reset:
            if st.button("🔄 Dosyayı Sıfırla", use_container_width=True):
                st.session_state['excel_results'] = {}
                st.session_state['uploader_key'] += 1
                st.rerun()

def new_control_page(user):
    st.header("Yeni Model Ölçü Kontrolü (Manuel)")
    # (Bu kısım önceki kodla aynı, özet geçiyorum)
    with st.expander("ℹ️ İşlem Bilgisi", expanded=True):
        c1, c2 = st.columns(2)
        with c1: business_unit = st.selectbox("BU Seçiniz", ["BU1", "BU3", "BU5"])
        with c2: slot_count = st.number_input("Parça Sayısı", 1, 5, 1)
        if st.session_state.get('active_session'):
            st.info(f"Model: {st.session_state['current_model'].get('model_adi')}")

    st.divider()
    tabs = st.tabs([f"Parça {i+1}" for i in range(slot_count)])
    inputs = {}
    for i, tab in enumerate(tabs):
        with tab:
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Gerber")
                inputs[f"g_cevre_{i}"] = st.text_area("Çevre", height=100, key=f"g_cevre_{i}")
                inputs[f"g_en_{i}"] = st.text_area("En", height=100, key=f"g_en_{i}")
                inputs[f"g_boy_{i}"] = st.text_area("Boy", height=100, key=f"g_boy_{i}")
            with c2:
                st.subheader("Polypattern")
                inputs[f"poly_{i}"] = st.text_area("Çıktı", height=340, key=f"poly_{i}")

    st.markdown("---")
    if st.button("🔍 Analiz Et", type="primary", use_container_width=True):
        st.session_state['analysis_results'] = {}
        for i in range(slot_count):
            g_c = inputs[f"g_cevre_{i}"]
            g_e = inputs[f"g_en_{i}"]
            g_b = inputs[f"g_boy_{i}"]
            p = inputs[f"poly_{i}"]
            
            if not (g_c and g_e and g_b and p): continue
            
            if 'active_session' not in st.session_state:
                meta = parse_gerber_metadata(g_c)
                if meta:
                    st.session_state['active_session'] = True
                    st.session_state['current_model'] = {"model_adi": meta['model_adi'], "sezon": meta['sezon'], "bu": business_unit}
            
            local_meta = parse_gerber_metadata(g_c)
            p_name = local_meta['parca_adi'] if local_meta else f"Parça {i+1}"
            
            df_gc = parse_gerber_table(g_c, 'cevre')
            df_ge = parse_gerber_table(g_e, 'en')
            df_gb = parse_gerber_table(g_b, 'boy')
            df_p = parse_polypattern(p)
            
            if not df_gc.empty and not df_ge.empty and not df_gb.empty and not df_p.empty:
                try:
                    df_t = df_gc.merge(df_ge, on="Beden").merge(df_gb, on="Beden")
                    df_f = df_t.merge(df_p, on="Beden")
                    df_f['Fark_Boy'] = (df_f['boy'] - df_f['poly_boy']).abs()
                    df_f['Fark_En'] = (df_f['en'] - df_f['poly_en']).abs()
                    df_f['Fark_Cevre'] = (df_f['cevre'] - df_f['poly_cevre']).abs()
                    
                    st.session_state['analysis_results'][i] = {"df": df_f, "parca_adi": p_name, "saved": False}
                except: st.error(f"Parça {i+1} hatası.")

    if st.session_state.get('analysis_results'):
        for i, res in st.session_state['analysis_results'].items():
            if res['saved']: continue
            with st.expander(f"Sonuç: {res['parca_adi']}", expanded=True):
                st.dataframe(res['df'])
                if st.button(f"Listeye Ekle {i}", key=f"btn_{i}"):
                    # Kayıt mantığı (kısalttım)
                    st.session_state['model_parts'].append({"parca_adi": res['parca_adi'], "durum": "Doğru", "timestamp": datetime.now()})
                    st.session_state['analysis_results'][i]['saved'] = True
                    st.rerun()

    if st.session_state.get('active_session') and st.session_state['model_parts']:
        if st.button("Bitir ve Kaydet"):
            save_to_firestore(user, business_unit)

def history_page():
    st.header("📋 Geçmiş")
    if not db: return
    docs = db.collection('qc_records').order_by('tarih', direction=firestore.Query.DESCENDING).limit(50).stream()
    data = [d.to_dict() for d in docs]
    if data:
        df = pd.DataFrame(data)
        st.dataframe(df[['tarih', 'model_adi', 'genel_durum', 'parca_sayisi']])

if __name__ == "__main__":
    main()
