import feedparser
import requests
import schedule
import time
from datetime import datetime
import google.generativeai as genai 
from duckduckgo_search import DDGS 
import yfinance as yf
import pandas as pd
import os

# API anahtarları GitHub Secrets veya ortam değişkenlerinden okunur
GEMINI_API = os.getenv("GEMINI_API_KEY") 
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Gemini Yapılandırması 
genai.configure(api_key=GEMINI_API)
model = genai.GenerativeModel('gemini-1.5-flash')

RSS_FILE = "rss_sources.txt"

def get_rss_news():
    headlines = []
    with open(RSS_FILE) as f:
        sources = f.readlines()
    for url in sources:
        try:
            feed = feedparser.parse(url.strip())
            for entry in feed.entries[:5]:
                headlines.append(entry.title)
        except:
            pass
    return headlines[:120]

def get_macro_data():
    data = {}
    try:
        data["VIX"] = yf.Ticker("^VIX").history(period="1d")["Close"].iloc[-1]
        data["US10Y"] = yf.Ticker("^TNX").history(period="1d")["Close"].iloc[-1]
        data["DXY"] = yf.Ticker("DX-Y.NYB").history(period="1d")["Close"].iloc[-1]
        data["GOLD"] = yf.Ticker("GC=F").history(period="1d")["Close"].iloc[-1]
        data["OIL"] = yf.Ticker("CL=F").history(period="1d")["Close"].iloc[-1]
    except:
        pass
    return data

def duckduckgo_research():
    """Perplexity API yerine ücretsiz arama motoru işlevini görür"""
    try:
        query = "global markets, gold and oil prices financial risks summary today"
        with DDGS() as ddgs:
            results = [r['body'] for r in ddgs.text(query, max_results=3)]
        return "\n".join(results)
    except:
        return "Araştırma verisi alınamadı."

def analyze_news(news, macro, research):
    prompt = f"""
Sen bir finansal analiz uzmanısın.
Aşağıdaki haberleri, makro verileri ve araştırma notlarını kullanarak kapsamlı bir finansal analiz raporu hazırla.

Haberler:
{news}

Makro Veriler:
{macro}

Araştırma Notları:
{research}

Analizi tamamen TÜRKÇE yap.

Şu başlıkları kullan:

KRİTİK GELİŞMELER

OLASI FİNANSAL RİSKLER

MAKROEKONOMİK DURUM

YATIRIM FIRSATLARI

KÜRESEL RİSK SEVİYESİ (Düşük / Orta / Yüksek)

İZLENMESİ GEREKEN GÖSTERGELER

Önemsiz haberleri filtrele.
Sadece finans sistemi için önemli olanları analiz et.
"""
    # Gemini üretimi
    response = model.generate_content(prompt)
    return response.text

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": message
    })

def generate_report():
    news = get_rss_news()
    macro = get_macro_data()
    research = duckduckgo_research() # İsim tamamen güncellendi
    analysis = analyze_news(news, macro, research)

    report = f"""
🌍 GLOBAL FİNANS RAPORU

Tarih: {datetime.now()}

Makro Veriler
VIX: {macro.get('VIX', 0):.2f}
US10Y: {macro.get('US10Y', 0):.2f}
DXY: {macro.get('DXY', 0):.2f}
ALTIN: ${macro.get('GOLD', 0):.2f}
PETROL: ${macro.get('OIL', 0):.2f}

---
{analysis}
"""
    send_telegram(report)

schedule.every().day.at("09:00").do(morning_report)
schedule.every().day.at("15:00").do(evening_report)
schedule.every().day.at("22:00").do(night_report)

if __name__ == "__main__":
    generate_report()
   
