import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

# ========================================
# ADD YOUR GEMINI API KEY HERE
# ========================================
GEMINI_API_KEY = "adi_api"

# PID Constants
Kp = 2.0
Ki = 0.5
Kd = 1.0

# Mock DB for integral and derivative per farm
state_db = {}

# Crop Reference Moisture levels (Extended for Gujarat seasons)
CROP_REFS = {
    # Kharif / Monsoon 
    'kapas': {'initial': 60, 'development': 70, 'mid': 75, 'late': 40}, # Cotton
    'magfali': {'initial': 60, 'development': 75, 'mid': 85, 'late': 45}, # Groundnut
    'dhan': {'initial': 80, 'development': 90, 'mid': 95, 'late': 70}, # Rice
    'makai': {'initial': 50, 'development': 60, 'mid': 75, 'late': 45}, # Maize
    'bajri': {'initial': 40, 'development': 50, 'mid': 60, 'late': 30}, # Pearl Millet
    'tuver': {'initial': 50, 'development': 65, 'mid': 75, 'late': 40}, # Pigeon Pea
    'soyabean': {'initial': 55, 'development': 70, 'mid': 80, 'late': 45},
    'til': {'initial': 45, 'development': 55, 'mid': 65, 'late': 35}, # Sesame

    # Rabi / Winter
    'ghau': {'initial': 60, 'development': 70, 'mid': 80, 'late': 50}, # Wheat
    'jeeru': {'initial': 40, 'development': 50, 'mid': 55, 'late': 30}, # Cumin
    'rai': {'initial': 50, 'development': 60, 'mid': 70, 'late': 40}, # Mustard
    'chana': {'initial': 45, 'development': 55, 'mid': 65, 'late': 35}, # Gram
    'isabgol': {'initial': 40, 'development': 55, 'mid': 60, 'late': 35},
    'lasan': {'initial': 60, 'development': 70, 'mid': 80, 'late': 50}, # Garlic
    'dungli': {'initial': 60, 'development': 75, 'mid': 80, 'late': 45}, # Onion

    # Zaid / Summer
    'tarbuj': {'initial': 70, 'development': 85, 'mid': 90, 'late': 60}, # Watermelon
    'kharbuja': {'initial': 65, 'development': 80, 'mid': 85, 'late': 55}, # Muskmelon
    'kakdi': {'initial': 70, 'development': 85, 'mid': 90, 'late': 60}, # Cucumber
    'charo': {'initial': 60, 'development': 75, 'mid': 85, 'late': 70}, # Fodder
    'shakbhaji': {'initial': 65, 'development': 80, 'mid': 85, 'late': 65} # Vegetables
}

def get_weather(lat, lon, api_key):
    if api_key == 'YOUR_OPENWEATHER_API_KEY' or not api_key:
        return {'temp': 32.0, 'humidity': 40.0, 'wind_speed': 5.0, 'desc': 'Cloudy', 'rain_prob': 0.1, 'mocked': True}
    
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units=metric"
    try:
        res = requests.get(url)
        data = res.json()
        if res.status_code == 200:
            return {
                'temp': data.get('main', {}).get('temp', 30.0),
                'humidity': data.get('main', {}).get('humidity', 50.0),
                'wind_speed': data.get('wind', {}).get('speed', 5.0),
                'desc': data.get('weather', [{}])[0].get('description', 'clear sky'),
                'rain_prob': data.get('rain', {}).get('1h', 0.0) > 0,
                'mocked': False
            }
        else:
            return {'temp': 30.0, 'humidity': 50.0, 'wind_speed': 5.0, 'desc': 'Error fetching', 'rain_prob': 0.1, 'mocked': True}
    except:
        return {'temp': 30.0, 'humidity': 50.0, 'wind_speed': 5.0, 'desc': 'Error fetching', 'rain_prob': 0.1, 'mocked': True}

def estimate_moisture(weather_data, soil_type):
    base_moisture = 50.0
    temp_factor = (weather_data['temp'] - 25) * 1.5
    humidity_factor = (weather_data['humidity'] - 50) * 0.2
    rain_factor = 20 if weather_data['rain_prob'] else 0
    
    # Soil intelligence
    if soil_type == 'sandy':
        base_moisture -= 10.0 # Sandy loses moisture fast
    elif soil_type == 'black':
        base_moisture += 15.0 # Black soil retains moisture well
        
    est = base_moisture - temp_factor + humidity_factor + rain_factor
    return max(0.0, min(100.0, est))

@app.route('/predict', methods=['POST'])
def predict_irrigation():
    data = request.json
    farm_id = data.get('farm_id', 'farm_1')
    lat = data.get('lat', 0.0)
    lon = data.get('lon', 0.0)
    crop = data.get('crop', 'ghau').lower()
    stage = data.get('stage', 'mid').lower()
    api_key = data.get('api_key', '')
    
    last_irrigation_days = data.get('last_irrigation_days', -1)
    soil = data.get('soil', 'loam').lower()
    
    weather = get_weather(lat, lon, api_key)
    Mest = estimate_moisture(weather, soil)
    
    crop_info = CROP_REFS.get(crop, CROP_REFS['ghau'])
    Mref = crop_info.get(stage, 60)
    
    error = Mref - Mest
    
    if farm_id not in state_db:
        state_db[farm_id] = {'integral': 0.0, 'prev_error': 0.0}
    
    state = state_db[farm_id]
    state['integral'] += error
    state['integral'] = max(-50, min(50, state['integral']))
    
    derivative = error - state['prev_error']
    state['prev_error'] = error
    
    output = (Kp * error) + (Ki * state['integral']) + (Kd * derivative)
    duration_minutes = max(0, output)
    
    # Adaptive control for soil
    if soil == 'sandy':
        duration_minutes *= 1.15
    elif soil == 'black':
        duration_minutes *= 0.85
        
    # Overrides and Alerts
    alerts = []
    fert_alert = None
    
    is_heavy_rain = weather.get('rain_prob') and weather.get('humidity') > 80
    
    if weather['temp'] > 38.0:
        # Heat stress -> Light watering even if error is small
        if duration_minutes < 15:
            duration_minutes = 15.0
        alerts.append({
            'type': 'heat_stress',
            'gu': '⚠ પાક ગરમી તણાવમાં છે હલકું પાણી આપો',
            'en': '⚠ Crop is under heat stress. Provide light irrigation.',
            'color': '#ef4444' # red
        })
    elif is_heavy_rain:
        duration_minutes = 0.0
        alerts.append({
            'type': 'heavy_rain',
            'gu': '⚠ ભારે વરસાદની શક્યતા, પાણી બંધ રાખો',
            'en': '⚠ Heavy rain expected. Keep irrigation OFF.',
            'color': '#3b82f6' # blue
        })
        
    # History based overrides
    if last_irrigation_days == 0 or last_irrigation_days == 1:
        # Irrigated yesterday or today
        duration_minutes = 0.0
        Mest += 20 # Artificial bump to hide error visually
        alerts.append({
            'type': 'recently_irrigated',
            'gu': '✅ તાજેતરમાં પિયત આપેલ છે, આજે જરૂર નથી',
            'en': '✅ Irrigated recently. No need today.',
            'color': '#10b981' # green
        })
    elif last_irrigation_days > 7 and weather['temp'] > 25 and not is_heavy_rain:
        duration_minutes += 15.0
        alerts.append({
            'type': 'dry_streak',
            'gu': '⚠ ઘણા દિવસથી પાણી નથી આપ્યું, ખાધ પૂરી કરો',
            'en': '⚠ Dry streak. Need extra water today.',
            'color': '#f59e0b' # orange
        })

    if stage in ['development', 'mid'] and not is_heavy_rain:
        fert_alert = {
            'gu': 'આ અઠવાડિયે ખાતર આપવું યોગ્ય છે (Growth Stage)',
            'en': 'Good time to apply fertilizer this week (Growth Stage)'
        }
        
    action = 'IRRIGATE' if duration_minutes > 5 else 'NO_ACTION'
    
    decision = {
        'Mref': Mref,
        'Mest': round(Mest, 2),
        'error': round(error, 2),
        'recommended_duration_mins': round(duration_minutes, 2),
        'weather': weather,
        'action': action,
        'alerts': alerts,
        'fert_alert': fert_alert
    }
    
    return jsonify(decision)


@app.route('/chat', methods=['POST'])
def ai_chat():
    data = request.json
    user_msg = data.get('message', '')
    crop = data.get('crop', '')
    gemini_key = GEMINI_API_KEY
    
    if not gemini_key:
        return jsonify({"reply": "માફ કરશો, AI સેવાનો ઉપયોગ કરવા માટે સર્વરમાં API Key સેટ કરેલ નથી."})
        
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
        prompt = f"""
        Act as a highly helpful and friendly agriculture AI assistant for a farmer in Gujarat. Respond only in Gujarati language.
        Keep answers relatively short, simple to understand, and relevant strictly to farming, crops, soil, markets, weather, and agriculture.
        IMPORTANT RULE: If the user asks about anything absolutely NOT related to farming (e.g. coding, movies, politics, random unrelated questions), you MUST politey refuse and tell them you are an Agricultural Assistant.
        Context: The user is currently growing the crop '{crop}'. Use this for tailored advice if applicable.
        User Question: {user_msg}
        """
        
        parts_list = [{"text": prompt}]
        image_base64 = data.get('imageBase64', None)
        
        if image_base64:
            mime_type = "image/jpeg"
            base64_str = image_base64
            # Handle data URL format (e.g. data:image/png;base64,iVBOR...)
            if image_base64.startswith("data:"):
                header, base64_str = image_base64.split(",", 1)
                mime_type = header.split(";")[0].replace("data:", "")
            
            parts_list.append({
                "inlineData": {
                    "mimeType": mime_type,
                    "data": base64_str
                }
            })

        payload = {
            "contents": [{"parts": parts_list}]
        }
        res = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=20)
        if res.status_code == 200:
            res_data = res.json()
            text_response = res_data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            return jsonify({"reply": text_response})
        elif res.status_code == 429:
            # Too Many Requests / Quota Exceeded
            return jsonify({"reply": "માફ કરશો, હાલમાં ઘણા ખેડૂતો એકસાથે સિસ્ટમનો ઉપયોગ કરી રહ્યા હોવાથી સર્વર વ્યસ્ત છે. કૃપા કરીને થોડી સેકંડ પછી ફરી પૂછો."})
        else:
            return jsonify({"reply": f"📢 Feature Access Notice

AGRIBRINE એપની આ સુવિધા હાલમાં સક્રિય છે,
પરંતુ જરૂરી API service માટે subscription ઉપલબ્ધ ન હોવાથી
આ feature હાલમાં ઉપયોગ માટે ઉપલબ્ધ નથી.

આ સુવિધા આવનારી અપડેટમાં શરૂ કરવામાં આવશે.
તમારા સહકાર માટે આભાર 🙏

– Team AGRIBRINE
."})
    except Exception as e:
        print("Chat API Error:", e)
        return jsonify({"reply": "ઇન્ટરનેટ કે સર્વરની કોઈ ટેકનીકલ ખામી છે. કૃપા કરીને થોડી વાર પછી ફરી પ્રયાસ કરો."})

if __name__ == '__main__':
    app.run(port=5000, debug=True)


