import streamlit as st
import pandas as pd
import re
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore

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
# 2. PARSER FONKSİYONLARI
# --------------------------------------------------------------------------

def parse_gerber_metadata(text_block):
    """Gerber çıktısındaki (L1/UTJW-DW0DW22280-SP26-OBAS) formatından bilgi çeker."""
    if not text_block: return None
    pattern = r"L\d+\/([\w-]+)-([A-Z]{2}\d{2})-([A-Z0-9]+)"
    match = re.search(pattern, text_block)
    
    if match:
        return {
            "model_adi": match.group(1),
            "sezon": match.group(2),
            "parca_adi": match.group(3)
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

def parse_gerber_table(text, value_type):
    """Gerber verilerini işler."""
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
                columns = rest.split('\t')
                columns = [c.strip() for c in columns] 
            else:
                columns = re.split(r'\s+', rest)

            try:
                val = 0.0
                numeric_values = []
                for c in columns:
                    try:
                        if c and any(char.isdigit() for char in c):
                            numeric_values.append(clean_number(c))
                    except:
                        pass

                if value_type == 'cevre':
                    if numeric_values:
                        val = max(numeric_values)
                elif value_type == 'en': 
                    if '\t' in rest and len(columns) >= 4:
                         val = clean_number(columns[3]) 
                    else:
                        if len(numeric_values) >= 3:
                            for v in numeric_values[2:]:
                                if abs(v) > 1.0: 
                                    val = v
                                    break
                            if val == 0.0 and len(numeric_values) > 2:
                                val = numeric_values[2]
                elif value_type == 'boy': 
                     if len(numeric_values) > 1:
                         val = numeric_values[1]

                data.append({"Beden": beden, value_type: abs(val)})
            except:
                continue

    return pd.DataFrame(data)

def parse_polypattern(text):
    """Polypattern temiz tablosunu işler."""
    if not text: return pd.DataFrame()
    lines = text.strip().split('\n')
    data = []
    
    for line in lines:
        clean_line = line.replace("*", " ")
        parts = re.split(r'\s+', clean_line.strip())
        
        if len(parts) >= 4:
            if not parts[0][0].isdigit():
                try:
                    beden = parts[0]
                    poly_boy = clean_number(parts[1])
                    poly_en = clean_number(parts[2])
                    poly_cevre = clean_number(parts[3])
                    
                    data.append({
                        "Beden": beden,
                        "poly_boy": poly_boy,
                        "poly_en": poly_en,
                        "poly_cevre": poly_cevre
                    })
                except:
                    continue
    return pd.DataFrame(data)

# --------------------------------------------------------------------------
# 3. SAYFA DÜZENİ VE AKIŞ
# --------------------------------------------------------------------------

def main():
    if 'current_model' not in st.session_state:
        st.session_state['current_model'] = {}
    if 'model_parts' not in st.session_state:
        st.session_state['model_parts'] = [] 
    # Analiz sonuçlarını saklamak için bir sözlük (Key: slot index)
    if 'analysis_results' not in st.session_state:
        st.session_state['analysis_results'] = {}

    st.title("🏭 Kalıp Ölçü Kontrol Sistemi")
    st.sidebar.image("https://cdn-icons-png.flaticon.com/512/3022/3022329.png", width=100)
    
    user = st.sidebar.text_input("Kullanıcı Adı", "muhendis_user")
    
    menu = st.sidebar.radio("Menü", ["Yeni Ölçü Kontrolü", "Kontrol Listesi / Geçmiş"])

    if menu == "Yeni Ölçü Kontrolü":
        new_control_page(user)
    elif menu == "Kontrol Listesi / Geçmiş":
        history_page()

def new_control_page(user):
    st.header("Yeni Model Ölçü Kontrolü")

    # --- MODEL BİLGİSİ ---
    with st.expander("ℹ️ İşlem Bilgisi & Model Özeti", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            business_unit = st.selectbox("Business Unit (BU) Seçiniz", ["BU1", "BU3", "BU5"])
            # Kaç parça aynı anda girilecek?
            slot_count = st.number_input("Aynı anda girilecek parça sayısı", min_value=1, max_value=5, value=1, step=1)
        
        with col2:
            if st.session_state.get('active_session'):
                st.info(f"Aktif Model: **{st.session_state['current_model'].get('model_adi')}** | Sezon: **{st.session_state['current_model'].get('sezon')}**")
                
                # Eklenen parçalar
                if len(st.session_state['model_parts']) > 0:
                    st.write("📋 **Eklenen Parçalar:**")
                    for p in st.session_state['model_parts']:
                        durum_ikon = "✅" if p['durum'] == "Doğru" else "❌"
                        st.text(f"{durum_ikon} {p['parca_adi']}")

    st.divider()

    # --- DİNAMİK PARÇA GİRİŞ SLOTLARI ---
    # Tabs kullanarak slotları bölelim, böylece sayfa çok uzamaz
    tabs = st.tabs([f"Parça {i+1}" for i in range(slot_count)])
    
    # Giriş verilerini tutmak için
    inputs = {}

    for i, tab in enumerate(tabs):
        with tab:
            col_gerber, col_poly = st.columns([1, 1])
            with col_gerber:
                st.subheader(f"1. Gerber Verileri (Parça {i+1})")
                inputs[f"g_cevre_{i}"] = st.text_area("Gerber Çevre", height=100, key=f"g_cevre_{i}")
                inputs[f"g_en_{i}"] = st.text_area("Gerber En", height=100, key=f"g_en_{i}")
                inputs[f"g_boy_{i}"] = st.text_area("Gerber Boy", height=100, key=f"g_boy_{i}")
            
            with col_poly:
                st.subheader(f"2. Polypattern Verisi (Parça {i+1})")
                inputs[f"poly_{i}"] = st.text_area("Polypattern Çıktısı", height=340, key=f"poly_{i}")

    st.markdown("---")
    
    # --- TOPLU ANALİZ BUTONU ---
    if st.button("🔍 Tüm Parçaları Analiz Et", type="primary", use_container_width=True):
        # Her bir slotu tek tek analiz et ve sonuçları kaydet
        st.session_state['analysis_results'] = {} # Önceki sonuçları temizle
        
        for i in range(slot_count):
            g_cevre = inputs[f"g_cevre_{i}"]
            g_en = inputs[f"g_en_{i}"]
            g_boy = inputs[f"g_boy_{i}"]
            poly = inputs[f"poly_{i}"]

            # Eğer slot boşsa atla
            if not (g_cevre and g_en and g_boy and poly):
                continue

            # 1. Metadata (Model bilgisi al, ilk dolu parça yeterli)
            # Eğer model bilgisi henüz yoksa ilk dolu parçadan al
            if 'active_session' not in st.session_state:
                metadata = parse_gerber_metadata(g_cevre)
                if metadata:
                    st.session_state['active_session'] = True
                    st.session_state['current_model'] = {
                        "model_adi": metadata['model_adi'],
                        "sezon": metadata['sezon'],
                        "bu": business_unit
                    }

            # Bu parçanın kendi adı (Metadata'dan tekrar çekiyoruz çünkü parça adı değişiyor)
            local_meta = parse_gerber_metadata(g_cevre)
            parca_adi = local_meta['parca_adi'] if local_meta else f"Bilinmeyen Parça {i+1}"

            # 2. Parsing
            df_g_cevre = parse_gerber_table(g_cevre, 'cevre')
            df_g_en = parse_gerber_table(g_en, 'en')
            df_g_boy = parse_gerber_table(g_boy, 'boy')
            df_poly = parse_polypattern(poly)

            if df_g_cevre.empty or df_g_en.empty or df_g_boy.empty or df_poly.empty:
                st.toast(f"Parça {i+1} için veriler okunamadı!", icon="⚠️")
                continue

            try:
                # 3. Merge & Calculate
                df_total = df_g_cevre.merge(df_g_en, on="Beden").merge(df_g_boy, on="Beden")
                df_final = df_total.merge(df_poly, on="Beden", how="inner")
                
                df_final['Fark_Boy'] = (df_final['boy'] - df_final['poly_boy']).abs()
                df_final['Fark_En'] = (df_final['en'] - df_final['poly_en']).abs()
                df_final['Fark_Cevre'] = (df_final['cevre'] - df_final['poly_cevre']).abs()

                # Sonuçları state'e kaydet
                st.session_state['analysis_results'][i] = {
                    "df": df_final,
                    "parca_adi": parca_adi,
                    "saved": False # Henüz kaydedilmedi
                }
            except Exception as e:
                st.toast(f"Parça {i+1} hesaplanırken hata: {e}", icon="❌")

    # --- SONUÇLARI GÖSTERME (HER PARÇA İÇİN AYRI KUTU) ---
    if st.session_state.get('analysis_results'):
        st.subheader("📊 Analiz Sonuçları")
        
        # Sonuçları yine tablarda veya alt alta expanderlarda gösterebiliriz.
        # Kullanıcı "ayrı ayrı kaydet" dediği için alt alta expander daha net görünür.
        
        results = st.session_state['analysis_results']
        
        for i in sorted(results.keys()):
            res = results[i]
            # Eğer bu parça zaten kaydedildiyse gösterme veya "Kaydedildi" de.
            if res.get('saved'):
                continue
                
            df_final = res['df']
            parca_adi = res['parca_adi']
            
            tolerans = 0.05
            hatali_satirlar = df_final[
                (df_final['Fark_Boy'] > tolerans) | 
                (df_final['Fark_En'] > tolerans) | 
                (df_final['Fark_Cevre'] > tolerans)
            ]
            hata_var = not hatali_satirlar.empty
            
            # Kart Görünümü (Expander)
            status_emoji = "⚠️" if hata_var else "✅"
            with st.expander(f"{status_emoji} Sonuç: {parca_adi} (Slot {i+1})", expanded=True):
                
                # Tablo
                numeric_cols = ['boy', 'poly_boy', 'en', 'poly_en', 'cevre', 'poly_cevre', 'Fark_Boy', 'Fark_En', 'Fark_Cevre']
                existing_cols = [c for c in numeric_cols if c in df_final.columns]
                
                st.dataframe(
                    df_final.style
                    .format("{:.2f}", subset=existing_cols)
                    .map(
                        lambda x: 'background-color: #ffcccc' if isinstance(x, (int, float)) and abs(x) > tolerans else '',
                        subset=['Fark_Boy', 'Fark_En', 'Fark_Cevre']
                    ),
                    use_container_width=True
                )

                if hata_var:
                    st.error(f"{len(hatali_satirlar)} bedende fark var.")
                else:
                    st.success("Ölçüler uyumlu.")

                # KAYDET BUTONU
                # Her butonun key'i benzersiz olmalı
                if st.button(f"💾 {parca_adi} - Listeye Ekle", key=f"save_btn_{i}"):
                    part_record = {
                        "parca_adi": parca_adi,
                        "durum": "Hatalı" if hata_var else "Doğru",
                        "hata_detayi": hatali_satirlar[['Beden', 'Fark_Boy', 'Fark_En', 'Fark_Cevre']].to_dict('records') if hata_var else [],
                        "timestamp": datetime.now()
                    }
                    st.session_state['model_parts'].append(part_record)
                    
                    # Bu sonucu "kaydedildi" olarak işaretle ki ekrandan gitsin veya pasif olsun
                    st.session_state['analysis_results'][i]['saved'] = True
                    st.success(f"{parca_adi} eklendi!")
                    st.rerun()

    # --- MODELİ VERİTABANINA YAZMA ---
    if st.session_state.get('active_session') and len(st.session_state['model_parts']) > 0:
        st.markdown("---")
        
        # Kaydedilmemiş analizler var mı uyarısı
        unsaved_count = 0
        if 'analysis_results' in st.session_state:
            unsaved_count = sum(1 for k, v in st.session_state['analysis_results'].items() if not v.get('saved'))
        
        if unsaved_count > 0:
            st.warning(f"⚠️ Yukarıda analiz edilmiş ancak henüz 'Listeye Ekle' denmemiş {unsaved_count} parça var.")

        col_final1, col_final2 = st.columns([3, 1])
        with col_final1:
            st.info(f"**Toplam Eklenen Parça:** {len(st.session_state['model_parts'])}")
        
        with col_final2:
            if st.button("🏁 Tüm Model İşlemini Bitir ve Kaydet", type="primary", use_container_width=True):
                save_to_firestore(user, business_unit)

def save_to_firestore(user, bu):
    if not db:
        st.warning("Veritabanı bağlantısı yok. Simülasyon yapıldı.")
    else:
        model_data = st.session_state['current_model']
        parts = st.session_state['model_parts']
        
        genel_durum = "Doğru Çevrilmiş"
        for p in parts:
            if p['durum'] == "Hatalı":
                genel_durum = "Hatalı"
                break
                
        doc_ref = db.collection('qc_records').document()
        doc_ref.set({
            'kullanici': user,
            'tarih': datetime.now(),
            'business_unit': bu,
            'model_adi': model_data.get('model_adi'),
            'sezon': model_data.get('sezon'),
            'parca_sayisi': len(parts),
            'genel_durum': genel_durum,
            'parca_detaylari': parts
        })
        st.balloons()
        st.success("Model başarıyla kaydedildi!")
    
    # State Temizleme
    st.session_state['model_parts'] = []
    st.session_state['current_model'] = {}
    st.session_state['analysis_results'] = {}
    del st.session_state['active_session']
    
    # Sayfa yenile (Inputlar temizlensin diye)
    st.rerun()

def history_page():
    st.header("📋 Model Kontrol Listesi")
    if not db:
        st.warning("Veritabanı bağlı değil.")
        return

    col1, col2 = st.columns(2)
    search_term = col1.text_input("Model veya Kullanıcı Ara")
    
    try:
        docs = db.collection('qc_records').order_by('tarih', direction=firestore.Query.DESCENDING).limit(50).stream()
        data = []
        for doc in docs:
            data.append(doc.to_dict())
        df = pd.DataFrame(data)
        
        if not df.empty:
            if 'tarih' in df.columns:
                df['tarih'] = pd.to_datetime(df['tarih']).dt.strftime('%Y-%m-%d %H:%M')
            if search_term:
                df = df[df['model_adi'].str.contains(search_term, case=False, na=False) | 
                        df['kullanici'].str.contains(search_term, case=False, na=False)]
            
            st.dataframe(df[['tarih', 'kullanici', 'business_unit', 'model_adi', 'sezon', 'genel_durum', 'parca_sayisi']], use_container_width=True)
            
            selected_row = st.selectbox("Detayları Gör:", df['model_adi'].unique())
            if selected_row:
                detay = df[df['model_adi'] == selected_row].iloc[0]
                st.write(f"**Parça Detayları ({selected_row}):**")
                # Tablo formatında detay
                detay_df = pd.DataFrame(detay['parca_detaylari'])
                st.dataframe(detay_df[['parca_adi', 'durum', 'timestamp']])
        else:
            st.info("Kayıt yok.")
    except Exception as e:
        st.error(f"Hata: {e}")

if __name__ == "__main__":
    main()
