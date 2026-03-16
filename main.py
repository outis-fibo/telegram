import feedparser
import requests
import schedule
import time
import json
import os
import io
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import google.genai as genai
from ddgs import DDGS
import yfinance as yf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import numpy as np

load_dotenv()

GEMINI_API     = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")

client   = genai.Client(api_key=GEMINI_API)
RSS_FILE = "rss_sources.txt"
MEMORY_FILE = "weekly_memory.json"

# ── Alarm eşikleri ────────────────────────────────────────────────────────────
VIX_THRESHOLD          = 30
PRICE_CHANGE_THRESHOLD = 2.0

CENTRAL_BANK_KEYWORDS = [
    "federal reserve", "fed rate", "interest rate decision", "rate hike", "rate cut",
    "ecb decision", "european central bank", "bank of england", "boe rate",
    "merkez bankası", "faiz kararı", "powell", "lagarde"
]

# Sektör ETF'leri
SECTOR_ETFS = {
    "Teknoloji":    "XLK",
    "Enerji":       "XLE",
    "Finans":       "XLF",
    "Sağlık":       "XLV",
    "Sanayi":       "XLI",
    "Tüketim":      "XLY",
    "Hammadde":     "XLB",
    "Gayrimenkul":  "XLRE",
    "Kamu":         "XLU",
}

# ── Telegram yardımcıları ─────────────────────────────────────────────────────

def send_text(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for i in range(0, len(message), 4096):
        resp = requests.post(url, data={"chat_id": CHAT_ID, "text": message[i:i+4096]})
        result = resp.json()
        if not result.get("ok"):
            print(f"⚠️ Telegram metin hatası: {resp.text}")
        else:
            print(f"✅ Telegram mesaj gönderildi (karakter: {len(message[i:i+4096])})")

def send_photo(image_bytes, caption=""):
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    resp = requests.post(url,
                         data={"chat_id": CHAT_ID, "caption": caption},
                         files={"photo": ("chart.png", image_bytes, "image/png")})
    if not resp.json().get("ok"):
        print(f"⚠️ Telegram fotoğraf hatası: {resp.text}")

# ── Veri çekme ────────────────────────────────────────────────────────────────

def get_rss_news():
    print("📰 RSS haberleri çekiliyor...")
    headlines = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=8)
    with open(RSS_FILE) as f:
        sources = f.readlines()
    for url in sources:
        try:
            feed = feedparser.parse(url.strip())
            for entry in feed.entries[:10]:
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    entry_time = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    if entry_time >= cutoff:
                        headlines.append(entry.title)
                else:
                    headlines.append(entry.title)
        except:
            pass
    print(f"✅ {len(headlines)} haber bulundu.")
    return headlines[:120]

def get_macro_data():
    print("📊 Makro veriler çekiliyor...")
    data = {}
    tickers = {
        "VIX": "^VIX", "US10Y": "^TNX", "DXY": "DX-Y.NYB",
        "GOLD": "GC=F", "OIL": "CL=F", "SP500": "^GSPC", "BIST": "XU100.IS"
    }
    for key, ticker in tickers.items():
        try:
            data[key] = yf.Ticker(ticker).history(period="2d")["Close"]
        except:
            pass
    print("✅ Makro veriler alındı.")
    return data

def get_sector_data():
    print("📊 Sektör verileri çekiliyor...")
    result = {}
    for name, ticker in SECTOR_ETFS.items():
        try:
            hist = yf.Ticker(ticker).history(period="2d")["Close"]
            if len(hist) >= 2:
                chg = ((hist.iloc[-1] - hist.iloc[-2]) / hist.iloc[-2]) * 100
                result[name] = round(float(chg), 2)
        except:
            result[name] = 0.0
    print("✅ Sektör verileri alındı.")
    return result

def get_latest(series):
    try:
        return float(series.iloc[-1])
    except:
        return 0.0

def pct_change(series):
    try:
        cur = float(series.iloc[-1])
        prv = float(series.iloc[-2])
        return ((cur - prv) / prv) * 100
    except:
        return 0.0

def duckduckgo_research():
    print("🔍 DuckDuckGo araştırması yapılıyor...")
    try:
        with DDGS() as ddgs:
            results = [r['body'] for r in ddgs.text(
                "global markets gold oil prices financial risks today", max_results=3)]
        print("✅ Araştırma tamamlandı.")
        return "\n".join(results)
    except Exception as e:
        print(f"⚠️ Araştırma hatası: {e}")
        return "Araştırma verisi alınamadı."

def get_economic_calendar():
    print("📆 Ekonomik takvim çekiliyor...")
    try:
        with DDGS() as ddgs:
            results = [r['body'] for r in ddgs.text(
                "economic calendar this week key events fed ecb earnings", max_results=3)]
        return "\n".join(results)
    except:
        return ""

# ── Hafıza sistemi ────────────────────────────────────────────────────────────

def load_memory():
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return {}

def save_memory(data):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Hafıza kayıt hatası: {e}")

def update_memory(macro):
    memory = load_memory()
    today = datetime.now().strftime("%Y-%m-%d")
    memory[today] = {
        "VIX":   get_latest(macro.get("VIX", [0])),
        "GOLD":  get_latest(macro.get("GOLD", [0])),
        "OIL":   get_latest(macro.get("OIL", [0])),
        "SP500": get_latest(macro.get("SP500", [0])),
        "BIST":  get_latest(macro.get("BIST", [0])),
        "DXY":   get_latest(macro.get("DXY", [0])),
    }
    # Sadece son 14 günü tut
    keys = sorted(memory.keys())[-14:]
    memory = {k: memory[k] for k in keys}
    save_memory(memory)
    return memory

def build_weekly_comparison(memory):
    keys = sorted(memory.keys())
    if len(keys) < 2:
        return "Geçen hafta verisi henüz yok."
    today_key = keys[-1]
    week_ago_key = keys[0]
    today_d = memory[today_key]
    past_d  = memory[week_ago_key]
    lines = [f"🧠 HAFTALIK KARŞILAŞTIRMA ({week_ago_key} → {today_key})"]
    for key in ["VIX", "GOLD", "OIL", "SP500", "BIST", "DXY"]:
        if key in today_d and key in past_d:
            now = today_d[key]
            prv = past_d[key]
            chg = ((now - prv) / prv * 100) if prv else 0
            arrow = "📈" if chg > 0 else "📉"
            lines.append(f"  {arrow} {key}: {prv:.2f} → {now:.2f}  (%{chg:+.2f})")
    return "\n".join(lines)

# ── Gemini çağrıları ──────────────────────────────────────────────────────────

def gemini(prompt, max_tokens=1000):
    time.sleep(5)  # Rate limit koruması: 15 istek/dk
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt
    )
    return response.text.strip()

def score_headlines(headlines):
    print("🏅 Haberler puanlanıyor...")
    try:
        result = gemini(f"""
Aşağıdaki finansal haber başlıklarını 1-10 arasında önem skoruyla değerlendir.
Sadece skoru 7 ve üzeri olanları döndür.
Yanıtı şu formatta ver (başka hiçbir şey yazma):
SKOR | BAŞLIK

Haberler:
{chr(10).join(headlines)}
""")
        scored = []
        for line in result.split('\n'):
            if '|' in line:
                parts = line.split('|', 1)
                try:
                    scored.append((int(parts[0].strip()), parts[1].strip()))
                except:
                    pass
        scored.sort(reverse=True)
        out = [f"[{s}] {t}" for s, t in scored]
        print(f"✅ {len(out)} önemli haber seçildi.")
        return out if out else headlines[:30]
    except Exception as e:
        print(f"⚠️ Puanlama hatası: {e}")
        return headlines[:30]

def get_top3_news(scored_headlines):
    print("🎯 Top 3 haber özeti hazırlanıyor...")
    try:
        return gemini(f"""
Aşağıdaki önemli finansal haberlerden EN KRİTİK 3 tanesini seç.
Her biri için şu formatta TÜRKÇE yaz:

🔹 BAŞLIK
📌 Ne oldu: (1 cümle)
💡 Piyasaya etkisi: (1 cümle)

Haberler:
{chr(10).join(scored_headlines[:20])}
""")
    except:
        return ""

def get_sentiment_score(headlines, macro):
    print("🌡️ Piyasa duygu skoru hesaplanıyor...")
    try:
        vix  = get_latest(macro.get("VIX", [0]))
        sp   = pct_change(macro.get("SP500", [0, 0]))
        gold = pct_change(macro.get("GOLD", [0, 0]))
        result = gemini(f"""
Aşağıdaki verilere göre piyasa duygu skorunu 0-100 arasında ver.
0 = Aşırı Korku, 100 = Aşırı Açgözlülük

VIX: {vix:.2f}
S&P500 günlük değişim: %{sp:.2f}
Altın günlük değişim: %{gold:.2f}
Son haberler (ilk 10): {headlines[:10]}

Yanıtı SADECE şu formatta ver (başka hiçbir şey yazma):
SKOR: [0-100 arası sayı]
ETİKET: [Aşırı Korku / Korku / Nötr / Açgözlülük / Aşırı Açgözlülük]
GEREKÇE: [1 cümle Türkçe açıklama]
""")
        return result
    except:
        return "SKOR: 50\nETİKET: Nötr\nGEREKÇE: Veri alınamadı."

def get_morning_briefing(scored_headlines, macro, calendar_data):
    print("🔔 Sabah brifing hazırlanıyor...")
    try:
        return gemini(f"""
Bugün için finansal sabah brifing hazırla. Sadece bugün takip edilmesi gereken 5 kritik maddeyi TÜRKÇE yaz.
Her madde 1-2 cümle olsun. Emoji kullan. Net ve actionable olsun.

Güncel haberler: {scored_headlines[:15]}
VIX: {get_latest(macro.get('VIX',[0])):.2f}
SP500 değişim: %{pct_change(macro.get('SP500',[0,0])):.2f}
Ekonomik takvim: {calendar_data[:300]}

Format:
📌 BUGÜN TAKİP ET

1️⃣ ...
2️⃣ ...
3️⃣ ...
4️⃣ ...
5️⃣ ...
""")
    except:
        return ""

def get_geo_risk(headlines):
    print("🗺️ Coğrafi risk analizi yapılıyor...")
    try:
        return gemini(f"""
Aşağıdaki haberlere göre hangi ülkeler/bölgeler finansal risk taşıyor?
TÜRKÇE, kısa ve öz yaz. Her ülke için 1 satır, emoji ile.

Format:
🗺️ COĞRAFİ RİSK HARİTASI
🔴 [Ülke/Bölge]: [Risk nedeni]
🟡 [Ülke/Bölge]: [Risk nedeni]
🟢 [Ülke/Bölge]: [Fırsat]

Haberler: {chr(10).join(headlines[:30])}
""")
    except:
        return ""

def get_full_analysis(scored_news, macro, research):
    print("🤖 Gemini ana analiz yapıyor...")
    macro_summary = {k: f"{get_latest(macro.get(k,[0])):.2f}" for k in
                     ["VIX","US10Y","DXY","GOLD","OIL","SP500","BIST"]}
    result = gemini(f"""
Sen bir finansal analiz uzmanısın. Kapsamlı TÜRKÇE rapor hazırla.

Haberler (önem skoruyla): {scored_news}
Makro Veriler: {macro_summary}
Araştırma: {research}

Şu başlıkları kullan:
KRİTİK GELİŞMELER
OLASI FİNANSAL RİSKLER
MAKROEKONOMİK DURUM
YATIRIM FIRSATLARI
KÜRESEL RİSK SEVİYESİ (Düşük / Orta / Yüksek)
İZLENMESİ GEREKEN GÖSTERGELER
""")
    print("✅ Ana analiz tamamlandı.")
    return result

# ── Alarm sistemi ─────────────────────────────────────────────────────────────

def check_alerts(macro, headlines):
    print("🚨 Alarmlar kontrol ediliyor...")
    alerts = []
    vix = get_latest(macro.get("VIX", [0]))
    if vix > VIX_THRESHOLD:
        alerts.append(f"🔴 VIX ALARMI: {vix:.2f} — Panik seviyesi!")
    for key, label in [("GOLD","ALTIN"), ("OIL","PETROL")]:
        chg = pct_change(macro.get(key, [0,0]))
        if abs(chg) >= PRICE_CHANGE_THRESHOLD:
            alerts.append(f"{'📈' if chg>0 else '📉'} {label} ALARMI: %{chg:+.2f}")
    sp_chg = pct_change(macro.get("SP500", [0,0]))
    if sp_chg <= -1.5:
        alerts.append(f"📉 S&P500 ALARMI: %{sp_chg:+.2f} — Sert satış!")
    bist_chg = pct_change(macro.get("BIST", [0,0]))
    if bist_chg <= -2.0:
        alerts.append(f"📉 BIST100 ALARMI: %{bist_chg:+.2f}")
    for h in headlines:
        if any(kw in h.lower() for kw in CENTRAL_BANK_KEYWORDS):
            alerts.append(f"🏦 MERKEZ BANKASI HABERİ: {h}")
            break
    if alerts:
        send_text("⚠️ PİYASA ALARMI ⚠️\n\n" + "\n\n".join(alerts) +
                  f"\n\n🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        print(f"🚨 {len(alerts)} alarm gönderildi.")
    else:
        print("✅ Alarm yok.")

# ── Grafik üretimi ────────────────────────────────────────────────────────────

def chart_vix():
    try:
        print("📈 VIX grafiği...")
        vix_data = yf.Ticker("^VIX").history(period="30d")["Close"]
        fig, ax = plt.subplots(figsize=(10, 4))
        fig.patch.set_facecolor('#1a1a2e')
        ax.set_facecolor('#16213e')
        ax.plot(vix_data.index, vix_data.values, color='#e94560', linewidth=2, label='VIX')
        ax.axhline(y=30, color='#ffd700', linestyle='--', linewidth=1.5, label='Panik (30)')
        ax.axhline(y=20, color='#00b4d8', linestyle='--', linewidth=1, alpha=0.7, label='Normal (20)')
        ax.fill_between(vix_data.index, vix_data.values, alpha=0.2, color='#e94560')
        ax.set_title('VIX — Korku Endeksi (Son 30 Gün)', color='white', fontsize=13, pad=12)
        ax.tick_params(colors='#aaaaaa')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
        plt.xticks(rotation=30)
        for sp in ax.spines.values(): sp.set_edgecolor('#333355')
        ax.legend(facecolor='#1a1a2e', labelcolor='white', fontsize=9)
        ax.grid(True, alpha=0.15, color='white')
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=130, bbox_inches='tight')
        buf.seek(0); plt.close()
        return buf.read()
    except Exception as e:
        print(f"⚠️ VIX grafik hatası: {e}"); return None

def chart_macro_table(macro):
    try:
        print("📊 Makro tablo...")
        labels  = ["VIX","US10Y (%)","DXY","Altın ($)","Petrol ($)","S&P500","BIST100"]
        keys    = ["VIX","US10Y","DXY","GOLD","OIL","SP500","BIST"]
        values  = [get_latest(macro.get(k,[0])) for k in keys]
        changes = [pct_change(macro.get(k,[0,0])) for k in keys]
        fig, ax = plt.subplots(figsize=(10, 4))
        fig.patch.set_facecolor('#1a1a2e')
        ax.set_facecolor('#1a1a2e'); ax.axis('off')
        rows = [[l, f"{v:,.2f}", f"%{c:+.2f}"] for l,v,c in zip(labels,values,changes)]
        cell_colors = [['#1a1a2e','#1a1a2e','#1a3d2b' if c>0 else '#3d1a1a' if c<0 else '#1a1a2e']
                       for c in changes]
        table = ax.table(cellText=rows, colLabels=["Gösterge","Değer","Değişim (%)"],
                         cellLoc='center', loc='center', cellColours=cell_colors)
        table.auto_set_font_size(False); table.set_fontsize(11); table.scale(1.3, 2.0)
        for (row, col), cell in table.get_celld().items():
            cell.set_edgecolor('#333355')
            if row == 0:
                cell.set_facecolor('#0f3460')
                cell.set_text_props(color='white', fontweight='bold')
            else:
                txt = cell.get_text().get_text()
                color = '#00e676' if (col==2 and '+' in txt) else '#ff5252' if (col==2 and '-' in txt) else 'white'
                cell.set_text_props(color=color)
        ax.set_title('Makro Gostergeler', color='white', fontsize=13, pad=12)
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='#1a1a2e')
        buf.seek(0); plt.close()
        return buf.read()
    except Exception as e:
        print(f"⚠️ Makro tablo hatası: {e}"); return None

def chart_sector_heatmap(sector_data):
    try:
        print("🔥 Sektör ısı haritası...")
        names  = list(sector_data.keys())
        values = list(sector_data.values())
        max_abs = max(abs(v) for v in values) or 1

        fig, ax = plt.subplots(figsize=(10, 3))
        fig.patch.set_facecolor('#1a1a2e')
        ax.set_facecolor('#1a1a2e'); ax.axis('off')

        cols = 3
        rows = (len(names) + cols - 1) // cols
        for i, (name, val) in enumerate(zip(names, values)):
            r, c = divmod(i, cols)
            intensity = abs(val) / max_abs
            color = (0.1, 0.4 + 0.4 * intensity, 0.1) if val >= 0 else (0.4 + 0.4 * intensity, 0.1, 0.1)
            rect = mpatches.FancyBboxPatch(
                (c / cols + 0.01, 1 - (r + 1) / rows + 0.02),
                1 / cols - 0.02, 1 / rows - 0.04,
                boxstyle="round,pad=0.01", facecolor=color, edgecolor='#333355',
                transform=ax.transAxes
            )
            ax.add_patch(rect)
            ax.text(c / cols + 1 / (cols * 2), 1 - (r + 0.5) / rows,
                    f"{name}\n%{val:+.2f}",
                    ha='center', va='center', color='white',
                    fontsize=9, fontweight='bold', transform=ax.transAxes)

        ax.set_title('Sektor Isi Haritasi (Gunluk Degisim)', color='white', fontsize=13, pad=12)
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='#1a1a2e')
        buf.seek(0); plt.close()
        return buf.read()
    except Exception as e:
        print(f"⚠️ Isı haritası hatası: {e}"); return None

def chart_sentiment_gauge(sentiment_text):
    try:
        print("🌡️ Duygu göstergesi...")
        score = 50
        for line in sentiment_text.split('\n'):
            if line.startswith("SKOR:"):
                try: score = int(line.split(":")[1].strip())
                except: pass

        fig, ax = plt.subplots(figsize=(8, 4), subplot_kw={'projection': 'polar'})
        fig.patch.set_facecolor('#1a1a2e')
        ax.set_facecolor('#1a1a2e')

        theta = np.linspace(0, np.pi, 200)
        colors = ['#e94560','#ff6b35','#ffd700','#90ee90','#00e676']
        for i, c in enumerate(colors):
            ax.bar(theta[i*40:(i+1)*40].mean(), 1, width=np.pi/5,
                   bottom=0.5, color=c, alpha=0.7, linewidth=0)

        needle_angle = np.pi * (1 - score / 100)
        ax.annotate('', xy=(needle_angle, 1.3), xytext=(needle_angle, 0.5),
                    arrowprops=dict(arrowstyle='->', color='white', lw=2.5))

        ax.set_ylim(0, 1.5)
        ax.set_theta_zero_location('W')
        ax.set_theta_direction(1)
        ax.set_xticks([0, np.pi/4, np.pi/2, 3*np.pi/4, np.pi])
        ax.set_xticklabels(['Aşırı\nAçgözlülük','Açgözlülük','Nötr','Korku','Aşırı\nKorku'],
                           color='white', fontsize=8)
        ax.set_yticks([])
        for sp in ax.spines.values(): sp.set_visible(False)

        label = "Nötr"
        for line in sentiment_text.split('\n'):
            if line.startswith("ETİKET:"): label = line.split(":",1)[1].strip()

        ax.set_title(f'Piyasa Duygu Skoru: {score}/100 — {label}',
                     color='white', fontsize=12, pad=20)
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='#1a1a2e')
        buf.seek(0); plt.close()
        return buf.read()
    except Exception as e:
        print(f"⚠️ Duygu gösterge hatası: {e}"); return None

# ── Telegram bot komutları ────────────────────────────────────────────────────

def handle_commands():
    """Telegram'dan gelen komutları dinle."""
    url    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    offset_file = "telegram_offset.txt"

    offset = 0
    if os.path.exists(offset_file):
        try:
            with open(offset_file) as f:
                offset = int(f.read().strip())
        except:
            pass

    try:
        resp = requests.get(url, params={"offset": offset, "timeout": 5})
        updates = resp.json().get("result", [])
    except:
        return

    for update in updates:
        offset = update["update_id"] + 1
        try:
            msg  = update.get("message", {})
            text = msg.get("text", "").strip().lower()
            if not text:
                continue

            print(f"💬 Komut alındı: {text}")

            if text in ["/start", "/help"]:
                send_text(
                    "🤖 Finans Bot Komutları:\n\n"
                    "/rapor — Hemen rapor gönder\n"
                    "/alarm — Alarm durumunu kontrol et\n"
                    "/vix — VIX grafiği\n"
                    "/sektor — Sektör ısı haritası\n"
                    "/duygu — Piyasa duygu skoru\n"
                    "/hafiza — Haftalık karşılaştırma\n"
                    "/takvim — Ekonomik takvim\n"
                    "/ozet — Günün top 3 haberi"
                )
            elif text == "/rapor":
                send_text("⏳ Rapor hazırlanıyor, birkaç dakika bekleyin...")
                generate_report("📲 (Manuel)")
            elif text == "/alarm":
                macro = get_macro_data()
                headlines = get_rss_news()
                check_alerts(macro, headlines)
            elif text == "/vix":
                img = chart_vix()
                if img: send_photo(img, "📈 VIX — Korku Endeksi")
            elif text == "/sektor":
                sector = get_sector_data()
                img = chart_sector_heatmap(sector)
                if img: send_photo(img, "🔥 Sektör Isı Haritası")
            elif text == "/duygu":
                macro    = get_macro_data()
                headlines = get_rss_news()
                sentiment = get_sentiment_score(headlines, macro)
                send_text(f"🌡️ PİYASA DUYGU SKORU\n\n{sentiment}")
                img = chart_sentiment_gauge(sentiment)
                if img: send_photo(img, "🌡️ Duygu Göstergesi")
            elif text == "/hafiza":
                memory = load_memory()
                send_text(build_weekly_comparison(memory))
            elif text == "/takvim":
                cal = get_economic_calendar()
                result = gemini(f"Bu haftaki önemli ekonomik olayları TÜRKÇE özetle, madde madde:\n{cal}")
                send_text(f"📆 EKONOMİK TAKVİM\n\n{result}")
            elif text == "/ozet":
                headlines = get_rss_news()
                scored    = score_headlines(headlines)
                top3      = get_top3_news(scored)
                send_text(f"🎯 GÜNÜN EN ÖNEMLİ 3 HABERİ\n\n{top3}")
            else:
                # Serbest soru — Gemini'ye sor
                macro     = get_macro_data()
                headlines = get_rss_news()
                answer    = gemini(f"""
Sen bir finansal analiz botusun. Kullanıcının sorusunu TÜRKÇE, kısa ve net yanıtla.

Güncel bağlam:
- VIX: {get_latest(macro.get('VIX',[0])):.2f}
- SP500 değişim: %{pct_change(macro.get('SP500',[0,0])):.2f}
- Son haberler: {headlines[:5]}

Kullanıcı sorusu: {text}
""")
                send_text(f"🤖 {answer}")
        except Exception as e:
            print(f"⚠️ Komut işleme hatası: {e}")

    with open(offset_file, "w") as f:
        f.write(str(offset))

# ── Ana rapor ─────────────────────────────────────────────────────────────────

def generate_report(session_label=""):
    print(f"\n🚀 Rapor oluşturuluyor: {session_label}")

    headlines     = get_rss_news()
    macro         = get_macro_data()
    sector_data   = get_sector_data()
    research      = duckduckgo_research()
    calendar_data = get_economic_calendar()

    # Alarmlar
    check_alerts(macro, headlines)

    # Haber puanlama
    scored = score_headlines(headlines)

    # Hafıza güncelle
    memory   = update_memory(macro)
    week_cmp = build_weekly_comparison(memory)

    # Gemini içerikler
    top3      = get_top3_news(scored)
    sentiment = get_sentiment_score(headlines, macro)
    briefing  = get_morning_briefing(scored, macro, calendar_data)
    geo_risk  = get_geo_risk(headlines)
    analysis  = get_full_analysis(scored, macro, research)

    # Değerler
    vix   = get_latest(macro.get("VIX",[0]))
    us10y = get_latest(macro.get("US10Y",[0]))
    dxy   = get_latest(macro.get("DXY",[0]))
    gold  = get_latest(macro.get("GOLD",[0]))
    oil   = get_latest(macro.get("OIL",[0]))
    sp    = get_latest(macro.get("SP500",[0]))
    bist  = get_latest(macro.get("BIST",[0]))
    sp_c  = pct_change(macro.get("SP500",[0,0]))
    bist_c= pct_change(macro.get("BIST",[0,0]))
    gold_c= pct_change(macro.get("GOLD",[0,0]))
    oil_c = pct_change(macro.get("OIL",[0,0]))

    # Duygu skoru parse
    sentiment_label = "Nötr"
    for line in sentiment.split('\n'):
        if line.startswith("ETİKET:"): sentiment_label = line.split(":",1)[1].strip()

    report = f"""
🌍 GLOBAL FİNANS RAPORU {session_label}
📅 {datetime.now().strftime('%d.%m.%Y %H:%M')} (UTC+3)

{briefing}

━━━━━━━━━━━━━━━━━━━━━━━━
🎯 GÜNÜN EN ÖNEMLİ 3 HABERİ
{top3}

━━━━━━━━━━━━━━━━━━━━━━━━
📊 MAKRO VERİLER
• VIX:     {vix:.2f}
• US10Y:   {us10y:.2f}%
• DXY:     {dxy:.2f}
• Altın:   ${gold:.2f}  ({gold_c:+.2f}%)
• Petrol:  ${oil:.2f}  ({oil_c:+.2f}%)
• S&P500:  {sp:.2f}  ({sp_c:+.2f}%)
• BIST100: {bist:.2f}  ({bist_c:+.2f}%)

🌡️ Piyasa Duygu: {sentiment_label}
{sentiment}

━━━━━━━━━━━━━━━━━━━━━━━━
{geo_risk}

━━━━━━━━━━━━━━━━━━━━━━━━
{week_cmp}

━━━━━━━━━━━━━━━━━━━━━━━━
{analysis}
"""
    send_text(report)

    # Grafikler
    for img, caption in [
        (chart_vix(),                    "📈 VIX — Korku Endeksi (Son 30 Gün)"),
        (chart_macro_table(macro),       "📊 Makro Göstergeler Tablosu"),
        (chart_sector_heatmap(sector_data), "🔥 Sektör Isı Haritası"),
        (chart_sentiment_gauge(sentiment),  "🌡️ Piyasa Duygu Skoru"),
    ]:
        if img: send_photo(img, caption)

    print("🎉 Rapor tamamlandı!\n")

# ── Zamanlama ─────────────────────────────────────────────────────────────────

def morning_report():  generate_report("🌅 (Sabah Seansı)")
def evening_report():  generate_report("🌆 (Öğleden Sonra Seansı)")
def night_report():    generate_report("🌙 (Gece Seansı)")

if __name__ == "__main__":
    import sys
    # GitHub Actions ortamında sadece tek rapor gönder ve çık
    if os.getenv("GITHUB_ACTIONS"):
        print("🤖 GitHub Actions modu — tek rapor gönderiliyor...")
        generate_report("🤖 (Otomatik)")
        handle_commands()  # Bekleyen komutları da işle
    else:
        # Yerel çalıştırma — zamanlanmış döngü
        print("🤖 Finans botu başlatıldı!")
        print("💬 Telegram komutları aktif: /help yazın")
        schedule.every().day.at("06:00").do(morning_report)   # TR 09:00
        schedule.every().day.at("12:00").do(evening_report)   # TR 15:00
        schedule.every().day.at("19:00").do(night_report)     # TR 22:00
        schedule.every(2).minutes.do(handle_commands)         # Komut dinleyici
        generate_report("🚀 (Başlangıç)")
        while True:
            schedule.run_pending()
            time.sleep(60)
