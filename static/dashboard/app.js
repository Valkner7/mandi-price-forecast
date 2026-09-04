let map = null;
let userMarker = null;
let mandiMarkersGroup = null;

// Initialize Leaflet Map centered over Punjab
function initMap() {
    const mapElement = document.getElementById('map');
    if (!mapElement) return;

    const punjabCenter = [30.9010, 75.8573]; // Default center: Ludhiana
    map = L.map('map').setView(punjabCenter, 8);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    mandiMarkersGroup = L.layerGroup().addTo(map);
}

// Request User Location using Browser Geolocation API
function getUserLocation() {
    const statusText = document.getElementById('location-status');
    const detectBtn = document.getElementById('detect-location-btn');

    if (!navigator.geolocation) {
        if (statusText) statusText.innerText = "Geolocation is not supported by your browser.";
        return;
    }

    if (statusText) statusText.innerText = "Detecting your GPS location...";
    if (detectBtn) detectBtn.disabled = true;

    navigator.geolocation.getCurrentPosition(
        (position) => {
            const userLat = position.coords.latitude;
            const userLon = position.coords.longitude;
            if (statusText) statusText.innerText = `Location detected: ${userLat.toFixed(4)}, ${userLon.toFixed(4)}`;
            if (detectBtn) detectBtn.disabled = false;

            fetchNearbyMandis(userLat, userLon);
        },
        (error) => {
            if (statusText) statusText.innerText = "Unable to retrieve location. Please grant permission.";
            if (detectBtn) detectBtn.disabled = false;
            console.error("GPS Error:", error);
        }
    );
}

// Fetch Nearby Mandis from Backend API
async function fetchNearbyMandis(lat, lon) {
    try {
        const response = await fetch(`/api/nearby-mandis?lat=${lat}&lon=${lon}&limit=10`);
        const data = await response.json();

        if (data.mandis) {
            updateMapAndList(data.user_location, data.mandis);
        }
    } catch (err) {
        console.error("Failed to fetch nearby mandis:", err);
    }
}

// Update Map Markers & List UI
function updateMapAndList(userLoc, mandis) {
    // Populate the list regardless of whether the map loaded
    const listContainer = document.getElementById('nearby-mandis-list');
    if (listContainer) {
        listContainer.innerHTML = '';
        mandis.forEach((mandi, index) => {
            const listItem = document.createElement('li');
            listItem.innerHTML = `<strong>#${index + 1} ${mandi.mandi}</strong> (${mandi.district} Dist.) — <span>${mandi.distance_km} km away</span>`;
            listContainer.appendChild(listItem);
        });
    }

    // Map is optional — skip marker drawing if it failed to initialize
    if (!map || !mandiMarkersGroup) return;

    // Clear old markers
    mandiMarkersGroup.clearLayers();
    if (userMarker) map.removeLayer(userMarker);

    // Add User Marker (Red)
    const redIcon = L.icon({
        iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png',
        shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
        iconSize: [25, 41],
        iconAnchor: [12, 41],
        popupAnchor: [1, -34],
        shadowSize: [41, 41]
    });

    userMarker = L.marker([userLoc.lat, userLoc.lon], { icon: redIcon })
        .addTo(map)
        .bindPopup("<b>Your Current Location</b>")
        .openPopup();

    map.setView([userLoc.lat, userLoc.lon], 9);

    // Add Mandi Markers
    mandis.forEach((mandi) => {
        const marker = L.marker([mandi.latitude, mandi.longitude])
            .bindPopup(`<b>${mandi.mandi} Mandi</b><br>District: ${mandi.district}<br>Distance: ${mandi.distance_km} km`);
        mandiMarkersGroup.addLayer(marker);
    });
}

document.addEventListener('DOMContentLoaded', () => {
    try {
        initMap();
    } catch (err) {
        console.error("Map failed to initialize:", err);
    }

    const detectBtn = document.getElementById('detect-location-btn');
    if (detectBtn) {
        detectBtn.addEventListener('click', getUserLocation);
    }
});
