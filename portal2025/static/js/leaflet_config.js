console.log("✅ leaflet_config.js geladen");

window.addEventListener("map:init", function (event) {
    const map = event.detail.map;

    const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors'
    });

    const luchtfoto = L.tileLayer('https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        attribution: 'Tiles &copy; Esri'
    });

    const hybrid = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors &copy; Esri',
        opacity: 0.5
    });

    const baseLayers = {
        "OpenStreetMap": osm,
        "Luchtfoto": luchtfoto,
        "Hybrid": L.layerGroup([luchtfoto, hybrid]),
    };

    L.control.layers(baseLayers).addTo(map);
    osm.addTo(map);
});
