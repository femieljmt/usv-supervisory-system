(() => {
  "use strict";

  const TILE_SIZE = 256;
  const DEFAULT_CENTER = {lat: 2.38545, lon: 99.14738};
  const DEFAULT_ZOOM = 15;
  const MIN_ZOOM = 2;
  const MAX_ZOOM = 22;
  const MAX_NATIVE_ZOOM = 19;
  const STATE_COLORS = {
    NORMAL: "#0c7b79",
    GCS_LOST: "#c43e3e",
    RECOVERY: "#c97808",
    PIXHAWK_LOST: "#7c4d96",
  };

  function finite(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function validCoordinate(point) {
    const lat = finite(point?.lat);
    const lon = finite(point?.lon);
    return lat !== null && lon !== null && lat >= -85.05112878 && lat <= 85.05112878 && lon >= -180 && lon <= 180 && !(Math.abs(lat) < 1e-12 && Math.abs(lon) < 1e-12);
  }

  function project(lat, lon, zoom) {
    const latitude = clamp(Number(lat), -85.05112878, 85.05112878);
    const longitude = Number(lon);
    const scale = TILE_SIZE * (2 ** zoom);
    const sinLat = Math.sin(latitude * Math.PI / 180);
    return {
      x: ((longitude + 180) / 360) * scale,
      y: (0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI)) * scale,
    };
  }

  function unproject(x, y, zoom) {
    const scale = TILE_SIZE * (2 ** zoom);
    const lon = x / scale * 360 - 180;
    const n = Math.PI - (2 * Math.PI * y / scale);
    const lat = 180 / Math.PI * Math.atan(Math.sinh(n));
    return {lat, lon};
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[char]));
  }

  function formatNumber(value, digits = 6) {
    const parsed = finite(value);
    return parsed === null ? "—" : parsed.toFixed(digits);
  }

  function formatValue(value, digits = 1, suffix = "") {
    const parsed = finite(value);
    return parsed === null ? "—" : `${parsed.toFixed(digits)}${suffix}`;
  }

  function normalizeState(value) {
    const state = String(value || "NORMAL").toUpperCase();
    return STATE_COLORS[state] ? state : "NORMAL";
  }

  class USVGpsMap {
    constructor(container, options = {}) {
      if (!container) throw new Error("GPS map container tidak ditemukan");
      this.container = container;
      this.tileUrl = options.tileUrl || "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
      this.attribution = options.attribution || "© OpenStreetMap contributors";
      this.center = {...DEFAULT_CENTER};
      this.zoom = DEFAULT_ZOOM;
      this.follow = true;
      this.sessionKey = "";
      this.track = [];
      this.mission = [];
      this.guidedTarget = null;
      this.manualSegment = {points: [], start: null, end: null, active: false};
      this.latest = null;
      this.freshness = "NO_DATA";
      this.showMission = true;
      this.showTrack = true;
      this.showGuided = true;
      this.showManual = true;
      this.missionRevision = "";
      this.activeWaypointSeq = null;
      this._drag = null;
      this._tileGeneration = 0;
      this._tileErrors = 0;
      this._tileLoads = 0;
      this._lastFitSignature = "";
      this._resizeFrame = null;
      this._buildDom();
      this._bindInteractions();
      this._resizeObserver = new ResizeObserver(() => this._scheduleRender(true));
      this._resizeObserver.observe(this.container);
      this.render(true);
    }

    _buildDom() {
      this.container.innerHTML = "";
      this.container.classList.add("usv-slippy-map");

      this.base = document.createElement("div");
      this.base.className = "map-fallback-grid";
      this.tilePane = document.createElement("div");
      this.tilePane.className = "map-tile-pane";
      this.canvas = document.createElement("canvas");
      this.canvas.className = "map-overlay-canvas";
      this.markerPane = document.createElement("div");
      this.markerPane.className = "map-marker-pane";
      this.popup = document.createElement("div");
      this.popup.className = "map-popup hidden";
      this.tileStatus = document.createElement("div");
      this.tileStatus.className = "map-tile-status hidden";
      this.tileStatus.textContent = "Base map tidak tersedia; lintasan GPS tetap ditampilkan.";
      this.scale = document.createElement("div");
      this.scale.className = "map-scale";
      this.attributionEl = document.createElement("a");
      this.attributionEl.className = "map-attribution";
      this.attributionEl.href = "https://www.openstreetmap.org/copyright";
      this.attributionEl.target = "_blank";
      this.attributionEl.rel = "noopener noreferrer";
      this.attributionEl.textContent = this.attribution;

      this.container.append(this.base, this.tilePane, this.canvas, this.markerPane, this.popup, this.tileStatus, this.scale, this.attributionEl);
    }

    _bindInteractions() {
      this.container.addEventListener("pointerdown", (event) => {
        if (event.button !== 0 || event.target.closest("button, a, .map-popup")) return;
        const centerPx = project(this.center.lat, this.center.lon, this.zoom);
        this._drag = {x: event.clientX, y: event.clientY, centerPx};
        this._updateFollow(false);
        this.container.classList.add("dragging");
        this.container.setPointerCapture(event.pointerId);
      });

      this.container.addEventListener("pointermove", (event) => {
        if (!this._drag) return;
        const dx = event.clientX - this._drag.x;
        const dy = event.clientY - this._drag.y;
        this.center = unproject(this._drag.centerPx.x - dx, this._drag.centerPx.y - dy, this.zoom);
        this.render(false);
      });

      const finishDrag = (event) => {
        if (!this._drag) return;
        this._drag = null;
        this.container.classList.remove("dragging");
        try { this.container.releasePointerCapture(event.pointerId); } catch (_) { /* no-op */ }
      };
      this.container.addEventListener("pointerup", finishDrag);
      this.container.addEventListener("pointercancel", finishDrag);

      this.container.addEventListener("wheel", (event) => {
        event.preventDefault();
        const rect = this.container.getBoundingClientRect();
        const cursorX = event.clientX - rect.left;
        const cursorY = event.clientY - rect.top;
        const oldZoom = this.zoom;
        const nextZoom = clamp(oldZoom + (event.deltaY < 0 ? 1 : -1), MIN_ZOOM, MAX_ZOOM);
        if (nextZoom === oldZoom) return;
        const centerPx = project(this.center.lat, this.center.lon, oldZoom);
        const worldAtCursor = {
          x: centerPx.x + cursorX - rect.width / 2,
          y: centerPx.y + cursorY - rect.height / 2,
        };
        const ratio = 2 ** (nextZoom - oldZoom);
        const nextWorldAtCursor = {x: worldAtCursor.x * ratio, y: worldAtCursor.y * ratio};
        const nextCenterPx = {
          x: nextWorldAtCursor.x - cursorX + rect.width / 2,
          y: nextWorldAtCursor.y - cursorY + rect.height / 2,
        };
        this.zoom = nextZoom;
        this.center = unproject(nextCenterPx.x, nextCenterPx.y, nextZoom);
        this._updateFollow(false);
        this.render(true);
      }, {passive: false});

      this.container.addEventListener("dblclick", (event) => {
        if (event.target.closest("button, a, .map-popup")) return;
        event.preventDefault();
        const rect = this.container.getBoundingClientRect();
        const point = this._coordinateAtPixel(
          event.clientX - rect.left,
          event.clientY - rect.top,
          rect,
        );
        this.center = point;
        this.zoom = clamp(this.zoom + 1, MIN_ZOOM, MAX_ZOOM);
        this._updateFollow(false);
        this.render(true);
      });

      this.container.addEventListener("click", (event) => {
        if (!event.target.closest(".map-marker")) this.hidePopup();
      });
    }

    destroy() {
      this._resizeObserver?.disconnect();
      this.container.innerHTML = "";
    }

    update({
      sessionKey,
      track,
      mission,
      missionRevision,
      guidedTarget,
      manualSegment,
      latest,
      freshness,
      activeWaypointSeq,
    }) {
      const newSession = sessionKey && sessionKey !== this.sessionKey;
      const nextMissionRevision = String(missionRevision || "");
      const missionChanged = Boolean(
        nextMissionRevision
        && this.missionRevision
        && nextMissionRevision !== this.missionRevision
      );

      this.sessionKey = sessionKey || this.sessionKey;
      this.track = (track || []).filter(validCoordinate).map((point) => ({
        ...point, lat: Number(point.lat), lon: Number(point.lon), state: normalizeState(point.state),
      }));
      this.mission = (mission || []).filter(validCoordinate).map((point) => ({
        ...point, lat: Number(point.lat), lon: Number(point.lon),
      }));
      this.guidedTarget = guidedTarget && validCoordinate(guidedTarget)
        ? {...guidedTarget, lat: Number(guidedTarget.lat), lon: Number(guidedTarget.lon)}
        : null;

      const manualPoints = (manualSegment?.points || []).filter(validCoordinate).map((point) => ({
        ...point, lat: Number(point.lat), lon: Number(point.lon),
      }));
      this.manualSegment = {
        ...(manualSegment || {}),
        points: manualPoints,
        start: manualSegment?.start && validCoordinate(manualSegment.start)
          ? {...manualSegment.start, lat: Number(manualSegment.start.lat), lon: Number(manualSegment.start.lon)}
          : (manualPoints[0] || null),
        end: manualSegment?.end && validCoordinate(manualSegment.end)
          ? {...manualSegment.end, lat: Number(manualSegment.end.lat), lon: Number(manualSegment.end.lon)}
          : (manualPoints.at(-1) || null),
      };
      this.latest = latest && validCoordinate(latest)
        ? {...latest, lat: Number(latest.lat), lon: Number(latest.lon)}
        : (this.track.at(-1) || null);
      this.freshness = String(freshness || "NO_DATA").toUpperCase();
      const parsedActive = Number(activeWaypointSeq);
      this.activeWaypointSeq = Number.isFinite(parsedActive) ? parsedActive : null;
      this.missionRevision = nextMissionRevision;

      const signature = `${this.sessionKey}:${this.track.length}:${this.mission.length}:${nextMissionRevision}`;
      if (newSession || !this._lastFitSignature) {
        if (!this.fitMission({force: true})) this.fitAll({force: true});
        this._lastFitSignature = signature;
      } else if (missionChanged) {
        // A newly uploaded mission replaces the old route and becomes the
        // primary viewport immediately, even when item count is unchanged.
        if (!this.fitMission({force: true})) this.fitAll({force: true});
        this._lastFitSignature = signature;
      } else if (this.follow && this.latest) {
        this._followCurrentIfNeeded();
      }
      this.render(false);
    }

    setLayerVisibility({mission, track, guided, manual}) {
      if (typeof mission === "boolean") this.showMission = mission;
      if (typeof track === "boolean") this.showTrack = track;
      if (typeof guided === "boolean") this.showGuided = guided;
      if (typeof manual === "boolean") this.showManual = manual;
      this.render(false);
    }

    setFollow(enabled) {
      this._updateFollow(Boolean(enabled));
      if (this.follow) this.centerCurrent();
      return this.follow;
    }

    _updateFollow(enabled) {
      const next = Boolean(enabled);
      if (this.follow === next) return;
      this.follow = next;
      this.container.dispatchEvent(new CustomEvent("usvmapfollowchange", {detail: {enabled: next}}));
    }

    zoomIn() {
      this.zoom = clamp(this.zoom + 1, MIN_ZOOM, MAX_ZOOM);
      this._updateFollow(false);
      this.render(true);
    }

    zoomOut() {
      this.zoom = clamp(this.zoom - 1, MIN_ZOOM, MAX_ZOOM);
      this._updateFollow(false);
      this.render(true);
    }

    centerCurrent({zoom = 20} = {}) {
      if (!this.latest) return false;
      this.center = {lat: this.latest.lat, lon: this.latest.lon};
      this.zoom = clamp(Math.max(this.zoom, zoom), MIN_ZOOM, MAX_ZOOM);
      this.render(true);
      return true;
    }

    fitMission({force = false} = {}) {
      if (!this.mission.length) return false;
      this.fitBounds(this.mission, {force, maxZoom: 21});
      return true;
    }

    fitTrack({force = false} = {}) {
      if (!this.track.length) return false;
      this.fitBounds(this.track, {force, maxZoom: 20});
      return true;
    }

    fitAll({force = false} = {}) {
      const points = [
        ...(this.showMission ? this.mission : []),
        ...(this.showTrack ? this.track : []),
      ].filter(validCoordinate);
      if (!points.length) return false;
      this.fitBounds(points, {force});
      return true;
    }

    fitBounds(points, {force = false, maxZoom = MAX_ZOOM} = {}) {
      const rect = this.container.getBoundingClientRect();
      if (rect.width < 20 || rect.height < 20) return;
      const lats = points.map((point) => Number(point.lat));
      const lons = points.map((point) => Number(point.lon));
      const minLat = Math.min(...lats);
      const maxLat = Math.max(...lats);
      const minLon = Math.min(...lons);
      const maxLon = Math.max(...lons);
      const center = {lat: (minLat + maxLat) / 2, lon: (minLon + maxLon) / 2};
      const padding = 70;
      const boundedMaxZoom = clamp(maxZoom, MIN_ZOOM, MAX_ZOOM);
      let selectedZoom = boundedMaxZoom;
      for (let zoom = boundedMaxZoom; zoom >= MIN_ZOOM; zoom -= 1) {
        const nw = project(maxLat, minLon, zoom);
        const se = project(minLat, maxLon, zoom);
        if ((se.x - nw.x) <= Math.max(40, rect.width - padding * 2) && (se.y - nw.y) <= Math.max(40, rect.height - padding * 2)) {
          selectedZoom = zoom;
          break;
        }
      }
      if (minLat === maxLat && minLon === maxLon) selectedZoom = Math.min(boundedMaxZoom, 20);
      this.center = center;
      this.zoom = selectedZoom;
      if (force) this._updateFollow(true);
      this.render(true);
    }

    _coordinateAtPixel(x, y, rect) {
      const centerPx = project(this.center.lat, this.center.lon, this.zoom);
      return unproject(
        centerPx.x + x - rect.width / 2,
        centerPx.y + y - rect.height / 2,
        this.zoom,
      );
    }

    _followCurrentIfNeeded() {
      if (!this.latest) return;
      const rect = this.container.getBoundingClientRect();
      const point = this._screenPoint(this.latest, rect);
      const marginX = rect.width * 0.27;
      const marginY = rect.height * 0.27;
      if (point.x < marginX || point.x > rect.width - marginX || point.y < marginY || point.y > rect.height - marginY) {
        this.center = {lat: this.latest.lat, lon: this.latest.lon};
      }
    }

    _scheduleRender(tiles) {
      if (this._resizeFrame) cancelAnimationFrame(this._resizeFrame);
      this._resizeFrame = requestAnimationFrame(() => {
        this._resizeFrame = null;
        this.render(tiles);
      });
    }

    render(forceTiles = false) {
      const rect = this.container.getBoundingClientRect();
      if (rect.width < 20 || rect.height < 20) return;
      this._resizeCanvas(rect);
      if (forceTiles || this._tileKey !== this._currentTileKey(rect)) this._renderTiles(rect);
      this._renderOverlay(rect);
      this._renderMarkers(rect);
      this._renderScale(rect);
    }

    _resizeCanvas(rect) {
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(1, Math.round(rect.width * ratio));
      const height = Math.max(1, Math.round(rect.height * ratio));
      if (this.canvas.width !== width || this.canvas.height !== height) {
        this.canvas.width = width;
        this.canvas.height = height;
      }
      this.canvas.style.width = `${rect.width}px`;
      this.canvas.style.height = `${rect.height}px`;
    }

    _currentTileKey(rect) {
      const centerPx = project(this.center.lat, this.center.lon, this.zoom);
      return `${this.zoom}:${Math.round(centerPx.x)}:${Math.round(centerPx.y)}:${Math.round(rect.width)}:${Math.round(rect.height)}`;
    }

    _tileSource(z, x, y) {
      return this.tileUrl.replace("{z}", String(z)).replace("{x}", String(x)).replace("{y}", String(y));
    }

    _renderTiles(rect) {
      this._tileKey = this._currentTileKey(rect);
      this._tileGeneration += 1;
      const generation = this._tileGeneration;
      this._tileErrors = 0;
      this._tileLoads = 0;
      this.tileStatus.classList.add("hidden");
      this.tilePane.innerHTML = "";

      // OpenStreetMap native tiles normally stop at z19. For closer field
      // inspection we over-zoom those tiles while keeping vector tracks and
      // markers at the requested z20-z22 resolution.
      const sourceZoom = Math.min(this.zoom, MAX_NATIVE_ZOOM);
      const scaleFactor = 2 ** (this.zoom - sourceZoom);
      const displayTileSize = TILE_SIZE * scaleFactor;
      const centerPx = project(this.center.lat, this.center.lon, this.zoom);
      const topLeft = {x: centerPx.x - rect.width / 2, y: centerPx.y - rect.height / 2};
      const minX = Math.floor(topLeft.x / displayTileSize);
      const maxX = Math.floor((topLeft.x + rect.width) / displayTileSize);
      const minY = Math.floor(topLeft.y / displayTileSize);
      const maxY = Math.floor((topLeft.y + rect.height) / displayTileSize);
      const tileCount = 2 ** sourceZoom;
      let requested = 0;

      for (let tileY = minY; tileY <= maxY; tileY += 1) {
        if (tileY < 0 || tileY >= tileCount) continue;
        for (let tileX = minX; tileX <= maxX; tileX += 1) {
          const wrappedX = ((tileX % tileCount) + tileCount) % tileCount;
          const img = document.createElement("img");
          img.className = "map-tile";
          img.alt = "";
          img.draggable = false;
          img.decoding = "async";
          img.style.left = `${tileX * displayTileSize - topLeft.x}px`;
          img.style.top = `${tileY * displayTileSize - topLeft.y}px`;
          img.style.width = `${displayTileSize + 1}px`;
          img.style.height = `${displayTileSize + 1}px`;
          img.src = this._tileSource(sourceZoom, wrappedX, tileY);
          requested += 1;
          img.addEventListener("load", () => {
            if (generation !== this._tileGeneration) return;
            this._tileLoads += 1;
            if (this._tileLoads > 0) this.tileStatus.classList.add("hidden");
          }, {once: true});
          img.addEventListener("error", () => {
            if (generation !== this._tileGeneration) return;
            this._tileErrors += 1;
            img.classList.add("failed");
            if (this._tileErrors >= requested && this._tileLoads === 0) this.tileStatus.classList.remove("hidden");
          }, {once: true});
          this.tilePane.appendChild(img);
        }
      }
      window.setTimeout(() => {
        if (generation === this._tileGeneration && this._tileLoads === 0 && this._tileErrors > 0) this.tileStatus.classList.remove("hidden");
      }, 3500);
    }

    _screenPoint(point, rect) {
      const centerPx = project(this.center.lat, this.center.lon, this.zoom);
      const world = project(point.lat, point.lon, this.zoom);
      const worldSize = TILE_SIZE * (2 ** this.zoom);
      let dx = world.x - centerPx.x;
      if (dx > worldSize / 2) dx -= worldSize;
      if (dx < -worldSize / 2) dx += worldSize;
      return {x: rect.width / 2 + dx, y: rect.height / 2 + (world.y - centerPx.y)};
    }

    _renderOverlay(rect) {
      const ratio = window.devicePixelRatio || 1;
      const ctx = this.canvas.getContext("2d");
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, rect.width, rect.height);
      ctx.lineCap = "round";
      ctx.lineJoin = "round";

      if (this.showMission && this.mission.length > 1) {
        ctx.save();
        ctx.strokeStyle = "#687c86";
        ctx.lineWidth = 3;
        ctx.setLineDash([9, 8]);
        this._drawPath(ctx, this.mission, rect);
        ctx.restore();
      }

      if (this.showTrack && this.track.length > 1) {
        const segments = [];
        let active = null;
        this.track.forEach((point) => {
          const state = normalizeState(point.state);
          if (!active || active.state !== state) {
            const carry = active?.points.at(-1);
            active = {state, points: carry ? [carry, point] : [point]};
            segments.push(active);
          } else active.points.push(point);
        });
        segments.forEach((segment) => {
          if (segment.points.length < 2) return;
          ctx.save();
          ctx.strokeStyle = STATE_COLORS[segment.state];
          ctx.lineWidth = 5;
          ctx.shadowColor = "rgba(255,255,255,.9)";
          ctx.shadowBlur = 2;
          this._drawPath(ctx, segment.points, rect);
          ctx.restore();
        });
      }

      if (this.showManual && this.manualSegment.points.length > 1) {
        ctx.save();
        ctx.strokeStyle = "#2563eb";
        ctx.lineWidth = 4;
        ctx.setLineDash([5, 5]);
        this._drawPath(ctx, this.manualSegment.points, rect);
        ctx.restore();
      }

      if (this.showGuided && this.guidedTarget && this.latest) {
        ctx.save();
        ctx.strokeStyle = "#a21caf";
        ctx.lineWidth = 3;
        ctx.setLineDash([7, 6]);
        this._drawPath(ctx, [this.latest, this.guidedTarget], rect);
        ctx.restore();
      }
    }

    _drawPath(ctx, points, rect) {
      let started = false;
      ctx.beginPath();
      points.forEach((point) => {
        const p = this._screenPoint(point, rect);
        if (!started) { ctx.moveTo(p.x, p.y); started = true; }
        else ctx.lineTo(p.x, p.y);
      });
      ctx.stroke();
    }

    _renderMarkers(rect) {
      this.markerPane.innerHTML = "";
      if (this.showMission) {
        this.mission.forEach((waypoint, index) => {
          const point = this._screenPoint(waypoint, rect);
          if (!this._inside(point, rect, 32)) return;
          const isHome = Boolean(waypoint.is_home) || Number(waypoint.seq) === 0;
          const sequence = Number(waypoint.seq ?? index + 1);
          const isActive = !isHome && this.activeWaypointSeq !== null && sequence === this.activeWaypointSeq;
          const isCompleted = !isHome && this.activeWaypointSeq !== null && sequence < this.activeWaypointSeq;
          const markerType = isHome ? "home" : `waypoint${isActive ? " active" : isCompleted ? " completed" : ""}`;
          const marker = this._marker(point, markerType, isHome ? "H" : String(sequence));
          marker.title = isHome ? "HOME" : `${isActive ? "ACTIVE " : ""}Waypoint ${sequence}`;
          marker.addEventListener("click", (event) => {
            event.stopPropagation();
            this.showPopup(point, `<strong>${isHome ? "HOME" : `${isActive ? "ACTIVE " : ""}Waypoint ${escapeHtml(sequence)}`}</strong><span>${escapeHtml(waypoint.command_name || waypoint.command || "NAV_WAYPOINT")}</span><span>Lat ${formatNumber(waypoint.lat, 7)}</span><span>Lon ${formatNumber(waypoint.lon, 7)}</span><span>Alt ${formatValue(waypoint.alt, 1, " m")}</span>`);
          });
          this.markerPane.appendChild(marker);
        });
      }

      if (this.showManual && this.manualSegment.start) {
        const startPoint = this._screenPoint(this.manualSegment.start, rect);
        if (this._inside(startPoint, rect, 32)) {
          const marker = this._marker(startPoint, "manual-start", "M");
          marker.title = "Manual segment start";
          marker.addEventListener("click", (event) => {
            event.stopPropagation();
            this.showPopup(startPoint, `<strong>MANUAL START</strong><span>Seq ${escapeHtml(this.manualSegment.start.seq_id ?? "—")}</span><span>Lat ${formatNumber(this.manualSegment.start.lat, 7)}</span><span>Lon ${formatNumber(this.manualSegment.start.lon, 7)}</span>`);
          });
          this.markerPane.appendChild(marker);
        }
      }

      if (this.showManual && this.manualSegment.end && !this.manualSegment.active) {
        const endPoint = this._screenPoint(this.manualSegment.end, rect);
        if (this._inside(endPoint, rect, 32)) {
          const marker = this._marker(endPoint, "manual-end", "E");
          marker.title = "Manual segment end";
          this.markerPane.appendChild(marker);
        }
      }

      if (this.showGuided && this.guidedTarget) {
        const guidedPoint = this._screenPoint(this.guidedTarget, rect);
        if (this._inside(guidedPoint, rect, 36)) {
          const marker = this._marker(guidedPoint, "guided", "G");
          marker.title = "Current GUIDED target";
          marker.addEventListener("click", (event) => {
            event.stopPropagation();
            this.showPopup(guidedPoint, `<strong>GUIDED TARGET</strong><span>Lat ${formatNumber(this.guidedTarget.lat, 7)}</span><span>Lon ${formatNumber(this.guidedTarget.lon, 7)}</span><span>Alt ${formatValue(this.guidedTarget.alt, 1, " m")}</span>`);
          });
          this.markerPane.appendChild(marker);
        }
      }

      if (this.latest && this.showTrack) {
        const point = this._screenPoint(this.latest, rect);
        if (this._inside(point, rect, 45)) {
          const marker = this._marker(point, this.freshness === "LIVE" ? "vehicle" : "last-known", "");
          const heading = finite(this.latest.heading) ?? 0;
          marker.style.setProperty("--heading", `${heading}deg`);
          marker.title = this.freshness === "LIVE" ? "Current vehicle position" : "Last known vehicle position";
          marker.addEventListener("click", (event) => {
            event.stopPropagation();
            this.showPopup(point, `<strong>${this.freshness === "LIVE" ? "Current Position" : "Last Known Position"}</strong><span>State ${escapeHtml(normalizeState(this.latest.state || this.latest.supervisor_state))}</span><span>Seq ${escapeHtml(this.latest.seq_id ?? "—")}</span><span>Lat ${formatNumber(this.latest.lat, 7)}</span><span>Lon ${formatNumber(this.latest.lon, 7)}</span><span>Speed ${formatValue(this.latest.speed ?? this.latest.groundspeed, 1, " m/s")}</span><span>Heading ${formatValue(this.latest.heading, 1, "°")}</span><span>GPS ${escapeHtml(this.latest.gps_fix_label || "—")} · HDOP ${formatValue(this.latest.gps_hdop, 2)}</span>`);
          });
          this.markerPane.appendChild(marker);
        }
      }
    }

    _marker(point, type, label) {
      const marker = document.createElement("button");
      marker.type = "button";
      marker.className = `map-marker ${type}`;
      marker.style.left = `${point.x}px`;
      marker.style.top = `${point.y}px`;
      marker.setAttribute("aria-label", label || type);
      marker.innerHTML = type === "vehicle" || type === "last-known"
        ? `<i></i>`
        : `<span>${escapeHtml(label)}</span>`;
      return marker;
    }

    _inside(point, rect, margin) {
      return point.x >= -margin && point.y >= -margin && point.x <= rect.width + margin && point.y <= rect.height + margin;
    }

    showPopup(point, html) {
      this.popup.innerHTML = `<button type="button" class="map-popup-close" aria-label="Tutup">×</button>${html}`;
      this.popup.style.left = `${clamp(point.x + 16, 10, Math.max(10, this.container.clientWidth - 240))}px`;
      this.popup.style.top = `${clamp(point.y - 20, 10, Math.max(10, this.container.clientHeight - 190))}px`;
      this.popup.classList.remove("hidden");
      this.popup.querySelector(".map-popup-close")?.addEventListener("click", () => this.hidePopup());
    }

    hidePopup() {
      this.popup.classList.add("hidden");
      this.popup.innerHTML = "";
    }

    _renderScale(rect) {
      const center = this.center;
      const metersPerPixel = 156543.03392 * Math.cos(center.lat * Math.PI / 180) / (2 ** this.zoom);
      const target = Math.max(1, metersPerPixel * Math.min(120, rect.width * 0.18));
      const magnitude = 10 ** Math.floor(Math.log10(target));
      const normalized = target / magnitude;
      const nice = normalized >= 5 ? 5 : normalized >= 2 ? 2 : 1;
      const meters = nice * magnitude;
      const pixels = meters / metersPerPixel;
      this.scale.style.width = `${Math.max(20, pixels)}px`;
      this.scale.textContent = meters >= 1000 ? `${(meters / 1000).toFixed(meters >= 10000 ? 0 : 1)} km` : `${Math.round(meters)} m`;
    }
  }

  window.USVGpsMap = USVGpsMap;
})();
