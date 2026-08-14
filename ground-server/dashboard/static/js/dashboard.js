(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const refreshMs = Math.max(1000, Number(window.DASHBOARD_REFRESH_SECONDS || 2) * 1000);
  const WIB_FORMAT = new Intl.DateTimeFormat("id-ID", {
    timeZone: "Asia/Jakarta", year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  let selectedVehicle = "";
  let selectedSession = "";
  let loading = false;
  let currentData = null;
  let gpsMap = null;
  let trackMode = "main";
  let recentTrackLimit = 500;
  let playbackTimer = null;
  const playback = {
    sessionKey: "",
    points: [],
    cursor: 0,
    loading: false,
    playing: false,
    speed: 1,
  };

  const STATE_COLORS = {
    NORMAL: "#17885a",
    GCS_LOST: "#b93b3b",
    RECOVERY: "#b76a0a",
    PIXHAWK_LOST: "#7c4d96",
  };

  function text(value, fallback = "—") {
    return value === null || value === undefined || value === "" ? fallback : String(value);
  }

  function number(value, digits = 1, fallback = "—") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(digits) : fallback;
  }

  function integer(value, fallback = "0") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.round(parsed).toLocaleString("id-ID") : fallback;
  }

  function localTime(value, fallback = "—") {
    if (!value) return fallback;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? text(value) : WIB_FORMAT.format(date).replaceAll(".", ":") + " WIB";
  }

  function duration(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value)) return "—";
    if (value < 1) return `${Math.round(value * 1000)} ms`;
    if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)} s`;
    if (value < 3600) return `${Math.floor(value / 60)}m ${Math.floor(value % 60)}s`;
    return `${Math.floor(value / 3600)}h ${Math.floor((value % 3600) / 60)}m`;
  }

  function clientFreshness(data) {
    const source = {...(data.freshness || {})};
    if (!data.session_is_latest && data.session_id) {
      source.status = "HISTORICAL";
      source.age_seconds = null;
      return source;
    }
    const reference = source.reference_at || source.last_received_at;
    const parsed = reference ? new Date(reference) : null;
    if (!parsed || Number.isNaN(parsed.getTime())) return source;
    const ageSeconds = Math.max(0, (Date.now() - parsed.getTime()) / 1000);
    source.age_seconds = ageSeconds;
    const live = Number(source.live_threshold_seconds || 5);
    const stale = Number(source.stale_threshold_seconds || 15);
    source.status = ageSeconds <= live ? "LIVE" : ageSeconds <= stale ? "STALE" : "OFFLINE";
    return source;
  }

  function escapeHtml(value) {
    return text(value, "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[char]));
  }

  function pill(value, forcedClass = "") {
    const label = text(value);
    const normalized = forcedClass || label.toLowerCase().replace(/[^a-z0-9]+/g, "_");
    return `<span class="pill ${escapeHtml(normalized)}">${escapeHtml(label)}</span>`;
  }

  function detailRows(rows) {
    return rows.map(([label, value, cls = ""]) =>
      `<div><span>${escapeHtml(label)}</span><strong class="${escapeHtml(cls)}">${value}</strong></div>`
    ).join("");
  }

  function setState(state, freshness) {
    const value = state || "NO DATA";
    const el = $("stateValue");
    el.textContent = value;
    el.className = `state-value ${(state || "neutral").toLowerCase()}`;
    if (!state) {
      $("stateContext").textContent = "Menunggu telemetry.";
    } else if (freshness === "LIVE") {
      $("stateContext").textContent = "Current state dari telemetry aktif.";
    } else {
      $("stateContext").textContent = `Last known state · data ${freshness || "UNKNOWN"}`;
    }
  }

  function setFreshness(freshness, requestOk = true) {
    const status = requestOk ? text(freshness?.status, "NO_DATA") : "SERVER OFFLINE";
    const badge = $("freshnessBadge");
    badge.textContent = status;
    const cls = status === "LIVE" ? "live" : status === "STALE" ? "stale" : status === "HISTORICAL" ? "historical" : status.includes("OFFLINE") ? "offline" : "neutral";
    badge.className = `badge ${cls}`;
    $("footerStatus").textContent = requestOk
      ? `Dashboard API online · data ${status}`
      : "Dashboard API unavailable";
  }

  function updateSelect(select, items, value, labelBuilder) {
    const signature = items.map((item) => typeof item === "string" ? item : `${item.session_id}:${labelBuilder(item)}`).join("|");
    if (select.dataset.signature !== signature) {
      select.innerHTML = "";
      if (!items.length) select.add(new Option("No data", ""));
      items.forEach((item) => {
        const key = typeof item === "string" ? item : String(item.session_id);
        select.add(new Option(labelBuilder(item), key));
      });
      select.dataset.signature = signature;
    }
    if (value) select.value = value;
  }

  function sessionLabel(item) {
    const finalState = text(item.final_state, "NO DATA");
    const rank = String(Number(item.rank || 0)).padStart(2, "0");
    const startedAt = localTime(item.session_started_at || item.first_record_at, item.session_id);
    return `[${rank}] ${startedAt} | ${integer(item.record_count)} rec | ${finalState}`;
  }

  function networkLabel(value) {
    return ({AVAILABLE: "UP", UNAVAILABLE: "DOWN", UNKNOWN: "UNKNOWN"})[value] || text(value);
  }

  function mqttLabel(value) {
    return ({CONNECTED: "UP", DISCONNECTED: "DOWN", CONNECTING: "CONNECTING"})[value] || text(value);
  }

  function lastKnownQualifier(freshness) {
    return freshness === "LIVE" ? "Current telemetry" : freshness === "NO_DATA" ? "No data" : "Last known value";
  }

  function renderOverview(data) {
    const latest = data.latest;
    const freshness = data.freshness || {};
    const summary = data.summary || {};
    const status = freshness.status || "NO_DATA";
    setState(latest?.supervisor_state, status);
    setFreshness(freshness, true);

    $("vehicleMeta").textContent = `Vehicle ${text(data.vehicle_id)}`;
    $("sessionMeta").textContent = `Session ${text(data.session_id)}`;
    $("lastUpdateMeta").textContent = `Last telemetry ${localTime(freshness.telemetry_at || freshness.last_received_at)}`;

    if (!latest) {
      clearLatestFields();
      renderEmptyDetails();
      return;
    }

    const qualifier = lastKnownQualifier(status);
    $("apValue").textContent = text(latest.ap_link);
    $("netValue").textContent = networkLabel(latest.internet_status);
    $("mqttValue").textContent = mqttLabel(latest.mqtt_connection_status);
    $("syncValue").textContent = text(latest.sync_status);
    ["apQualifier", "netQualifier", "mqttQualifier", "syncQualifier"].forEach((id) => $(id).textContent = qualifier);

    const sources = summary.data_sources || [latest.data_source];
    const sourceLabel = sources.join(" + ");
    $("sourceBadge").textContent = sourceLabel;
    $("sourceBadge").className = `source-badge ${sourceLabel.toUpperCase().includes("SIMULATION") ? "simulation" : ""}`;

    $("bufferValue").textContent = integer(latest.buffer_count);
    $("storedValue").textContent = integer(summary.total_records);
    $("ackValue").textContent = integer(summary.ack_send_count);
    $("replayValue").textContent = integer(summary.replay_records);
    $("duplicateValue").textContent = integer(summary.duplicate_deliveries);

    $("modeValue").textContent = text(latest.flight_mode);
    $("armedValue").textContent = latest.armed === true ? "ARMED" : latest.armed === false ? "DISARMED" : "—";
    $("speedValue").textContent = number(latest.groundspeed, 1);
    $("headingValue").textContent = number(latest.heading, 1);
    $("batteryValue").textContent = number(latest.battery_v, 1);
    $("batteryPercentValue").textContent = latest.battery_remaining_pct === null || latest.battery_remaining_pct === undefined
      ? "V" : `${integer(latest.battery_remaining_pct)}% · V`;
    $("gpsValue").textContent = text(latest.gps_fix_label);
    $("hdopValue").textContent = `HDOP ${number(latest.gps_hdop, 2)}`;
    $("waypointValue").textContent = `${integer(latest.wp_index)}/${integer(latest.wp_total)}`;
    $("distanceValue").textContent = `Distance ${number(latest.wp_dist, 1)} m`;
    $("dataAgeValue").textContent = duration(freshness.age_seconds);
    $("receivedValue").textContent = `Telemetry ${localTime(freshness.telemetry_at || freshness.last_received_at)}`;
    $("latValue").textContent = number(latest.lat, 7);
    $("lonValue").textContent = number(latest.lon, 7);
    $("positionQualifier").textContent = status === "LIVE" ? "Current position" : "Last known position";

    $("freshnessDetails").innerHTML = detailRows([
      ["Data status", pill(status, status.toLowerCase()), status === "LIVE" ? "status-ok" : status === "STALE" ? "status-warn" : "status-bad"],
      ["Data age", escapeHtml(duration(freshness.age_seconds))],
      ["Telemetry time", escapeHtml(localTime(freshness.telemetry_at || freshness.last_received_at))],
      ["Server receipt", escapeHtml(localTime(freshness.server_received_at))],
      ["Timestamp source", escapeHtml(text(freshness.timestamp_source))],
      ["Clock check", freshness.clock_skew_detected ? '<span class="clock-warning">CLOCK MISMATCH</span>' : "OK", freshness.clock_skew_detected ? "status-bad" : "status-ok"],
      ["LIVE threshold", `≤ ${number(freshness.live_threshold_seconds, 0)} seconds`],
      ["OFFLINE threshold", `> ${number(freshness.stale_threshold_seconds, 0)} seconds`],
      ["Interpretation", status === "LIVE" ? "Values are current" : status === "HISTORICAL" ? "Selected session is historical" : "Values are historical / last known"],
    ]);

    const missing = Number(summary.missing_records || 0);
    $("sequenceDetails").innerHTML = detailRows([
      ["Sequence range", `${text(summary.min_seq_id)} – ${text(summary.max_seq_id)}`],
      ["Expected unique", integer(summary.expected_records)],
      ["Stored unique", integer(summary.total_records)],
      ["Missing sequence", integer(missing), missing === 0 ? "status-ok" : "status-bad"],
      ["Continuity", summary.sequence_contiguous ? "CONTIGUOUS" : "GAPS DETECTED", summary.sequence_contiguous ? "status-ok" : "status-bad"],
      ["Missing IDs", summary.missing_sequences?.length ? escapeHtml(summary.missing_sequences.join(", ")) : "None"],
    ]);
  }

  function clearLatestFields() {
    ["apValue", "netValue", "mqttValue", "syncValue", "modeValue", "armedValue", "speedValue", "headingValue", "batteryValue", "gpsValue", "waypointValue", "dataAgeValue", "latValue", "lonValue"].forEach((id) => $(id).textContent = "—");
    $("bufferValue").textContent = "0";
    $("storedValue").textContent = "0";
    $("ackValue").textContent = "0";
    $("replayValue").textContent = "0";
    $("duplicateValue").textContent = "0";
    $("batteryPercentValue").textContent = "—";
    $("hdopValue").textContent = "HDOP —";
    $("distanceValue").textContent = "Distance —";
    $("receivedValue").textContent = "Received —";
    $("positionQualifier").textContent = "—";
    $("mapGpsFixValue").textContent = "—";
    $("mapHdopValue").textContent = "—";
    $("mapTrackCountValue").textContent = "0";
    $("mapModeValue").textContent = "—";
    $("mapGuidedValue").textContent = "—";
    $("gpsQualityBadge").textContent = "NO GPS DATA";
    $("gpsQualityBadge").className = "gps-quality-badge no-data";
  }

  function renderEmptyDetails() {
    $("freshnessDetails").innerHTML = detailRows([["Status", "NO TELEMETRY"], ["Interpretation", "Waiting for vehicle data"]]);
    $("sequenceDetails").innerHTML = detailRows([["Stored unique", "0"], ["Continuity", "Not available"]]);
  }

  function renderNotice(data) {
    const notices = [];
    const freshness = data.freshness?.status;
    const sources = data.summary?.data_sources || [];
    if (freshness === "OFFLINE") notices.push("Telemetry tidak aktif. Seluruh status yang tampil adalah nilai terakhir yang tersimpan.");
    if (freshness === "HISTORICAL") notices.push("Anda sedang melihat session historis. Pilih [01] untuk membuka session terbaru.");
    if (freshness === "STALE") notices.push("Telemetry terlambat. Periksa koneksi onboard atau tunggu pembaruan berikutnya.");
    if (sources.some((source) => String(source).toUpperCase().includes("SIMULATION"))) notices.push("Session ini menggunakan data SIMULATION dan tidak boleh digunakan sebagai hasil pengujian lapangan.");
    if (data.mission_availability === "MISSING_FOR_SESSION") notices.push("Mission snapshot tidak tersedia untuk session ini. Dashboard tidak mencampurkan mission dari session lain.");
    if (data.freshness?.clock_skew_detected) notices.push("Jam Raspberry Pi Lab tidak sinkron dengan timestamp telemetry. Waktu telemetry digunakan sebagai acuan tampilan.");
    const bar = $("noticeBar");
    if (notices.length) {
      bar.textContent = notices.join(" ");
      bar.classList.remove("hidden");
    } else {
      bar.textContent = "";
      bar.classList.add("hidden");
    }
  }

  function ensureGpsMap() {
    if (gpsMap) return gpsMap;
    if (typeof window.USVGpsMap !== "function") {
      console.error("USVGpsMap module tidak tersedia");
      return null;
    }
    const container = $("gpsMap");
    gpsMap = new window.USVGpsMap(container);
    container.addEventListener("usvmapfollowchange", (event) => {
      const enabled = Boolean(event.detail?.enabled);
      const button = $("mapFollowButton");
      button.setAttribute("aria-pressed", String(enabled));
      button.textContent = enabled ? "Follow ON" : "Follow OFF";
      button.classList.toggle("active", enabled);
    });
    return gpsMap;
  }

  function gpsQualityClass(status) {
    const value = String(status || "NO_DATA").toUpperCase();
    if (value === "GOOD") return "";
    if (value === "DEGRADED") return "degraded";
    if (value === "NO_FIX") return "no-fix";
    if (value === "UNKNOWN") return "unknown";
    return "no-data";
  }

  function currentTrackView(data) {
    if (trackMode === "playback" && playback.points.length) {
      const cursor = Math.min(Math.max(0, playback.cursor), playback.points.length - 1);
      return {
        track: playback.points.slice(0, cursor + 1),
        latest: playback.points[cursor],
        playback: true,
      };
    }
    return {track: data.track || [], latest: null, playback: false};
  }

  function waypointStatus(waypoint, activeSeq) {
    const seq = Number(waypoint?.seq);
    if (Boolean(waypoint?.is_home) || seq === 0) return "HOME";
    if (!Number.isFinite(activeSeq) || activeSeq <= 0) return "PENDING";
    if (seq === activeSeq) return "ACTIVE";
    return seq < activeSeq ? "COMPLETED" : "PENDING";
  }

  function renderMission(data) {
    const mission = data.mission;
    const latestTelemetry = data.latest;
    const view = currentTrackView(data);
    const track = view.track;
    const latest = view.latest || latestTelemetry;
    const waypoints = mission?.waypoints || [];
    const gpsQuality = data.gps_quality || {};
    const freshness = data.freshness?.status || "NO_DATA";
    const guidedTarget = data.guided_target || null;
    const manualSegment = data.manual_segment || {points: [], active: false};
    const activeWaypoint = Number(latest?.wp_index ?? latestTelemetry?.wp_index);

    $("missionProgress").innerHTML = detailRows([
      ["Mission snapshot", mission ? "AVAILABLE" : "NOT AVAILABLE", mission ? "status-ok" : "status-warn"],
      ["Mission status", escapeHtml(text(latestTelemetry?.mission_status))],
      ["Active waypoint", `${text(activeWaypoint, "0")} / ${text(latestTelemetry?.wp_total, "0")}`],
      ["Distance to waypoint", `${number(latestTelemetry?.wp_dist, 1)} m`],
      ["Mission complete", latestTelemetry?.mission_complete ? "YES" : "NO"],
      ["Mission records", integer(mission?.mission_total || waypoints.length)],
      ["Mission revision", text(mission?.revision, "—")],
      ["Mission generated", mission?.display_at ? localTime(mission.display_at) : "—"],
      ["Server receipt", mission?.received_at ? localTime(mission.received_at) : "—"],
      ["GUIDED target", guidedTarget ? `${number(guidedTarget.lat, 7)}, ${number(guidedTarget.lon, 7)}` : (String(latestTelemetry?.flight_mode).toUpperCase() === "GUIDED" ? "WAITING TARGET" : "NOT ACTIVE")],
    ]);

    const lastValidPosition = track.at(-1) || null;
    const gpsPositionCurrent = Boolean(
      lastValidPosition
      && freshness === "LIVE"
      && !view.playback
      && !["NO_FIX", "NO_DATA"].includes(String(gpsQuality.status || "NO_DATA"))
      && Number(lastValidPosition.seq_id) === Number(latestTelemetry?.seq_id)
    );
    const positionMeaning = view.playback
      ? "PLAYBACK POSITION"
      : gpsPositionCurrent
        ? "CURRENT GPS POSITION"
        : lastValidPosition ? "LAST VALID GPS POSITION" : "NO VALID POSITION";

    $("reconstructionStatus").innerHTML = detailRows([
      ["GPS quality", pill(gpsQuality.status || "NO_DATA", gpsQualityClass(gpsQuality.status)), gpsQuality.status === "GOOD" ? "status-ok" : gpsQuality.status === "DEGRADED" ? "status-warn" : "status-bad"],
      ["GPS fix", escapeHtml(text(lastValidPosition?.gps_fix_label || gpsQuality.fix_label || latestTelemetry?.gps_fix_label))],
      ["HDOP", number(lastValidPosition?.gps_hdop ?? gpsQuality.hdop ?? latestTelemetry?.gps_hdop, 2)],
      ["Displayed track points", integer(track.length)],
      ["Total valid track", integer(data.track_meta?.total_valid_points ?? track.length)],
      ["Rejected GPS points", integer(gpsQuality.rejected_track_points)],
      ["Mission coordinates", integer(waypoints.filter((wp) => wp.lat != null && wp.lon != null).length)],
      ["Mission session", mission ? "MATCHED" : "NOT AVAILABLE", mission ? "status-ok" : "status-warn"],
      ["Position meaning", positionMeaning],
      ["Latest MANUAL segment", manualSegment.points?.length ? `${integer(manualSegment.points.length)} points · ${manualSegment.active ? "ACTIVE" : "COMPLETED"}` : "NO SEGMENT"],
    ]);

    const rows = waypoints.map((wp) => {
      const status = waypointStatus(wp, activeWaypoint);
      const rowClass = status.toLowerCase();
      return `<tr class="waypoint-row ${rowClass}">
        <td>${escapeHtml(text(wp.seq))}</td>
        <td><span class="waypoint-status ${rowClass}">${status}</span></td>
        <td>${pill(wp.is_home ? "HOME" : "MISSION")}</td>
        <td>${escapeHtml(text(wp.command_name))}</td>
        <td>${number(wp.lat, 7)}</td>
        <td>${number(wp.lon, 7)}</td>
      </tr>`;
    }).join("");
    $("waypointRows").innerHTML = rows || `<tr><td colspan="6" class="empty-cell">Mission snapshot belum tersedia untuk session ini.</td></tr>`;

    $("mapGpsFixValue").textContent = text(lastValidPosition?.gps_fix_label || gpsQuality.fix_label || latestTelemetry?.gps_fix_label);
    $("mapHdopValue").textContent = number(lastValidPosition?.gps_hdop ?? gpsQuality.hdop ?? latestTelemetry?.gps_hdop, 2);
    $("mapTrackCountValue").textContent = integer(track.length);
    $("latValue").textContent = number(lastValidPosition?.lat, 7);
    $("lonValue").textContent = number(lastValidPosition?.lon, 7);
    $("positionQualifier").textContent = positionMeaning;
    $("mapModeValue").textContent = text(lastValidPosition?.flight_mode || latestTelemetry?.flight_mode);
    $("mapGuidedValue").textContent = guidedTarget ? `${number(guidedTarget.lat, 6)}, ${number(guidedTarget.lon, 6)}` : "—";
    const qualityBadge = $("gpsQualityBadge");
    qualityBadge.textContent = view.playback ? "PLAYBACK" : gpsQuality.status === "GOOD" ? "GPS GOOD" : gpsQuality.status === "DEGRADED" ? "GPS DEGRADED" : gpsQuality.status === "NO_FIX" ? "NO GPS FIX" : gpsQuality.status === "UNKNOWN" ? "GPS UNKNOWN" : "NO GPS DATA";
    qualityBadge.className = `gps-quality-badge ${view.playback ? "unknown" : gpsQualityClass(gpsQuality.status)}`.trim();

    const empty = $("mapEmpty");
    const hasCoordinates = track.length > 0 || waypoints.some((wp) => wp.lat != null && wp.lon != null);
    empty.style.display = hasCoordinates ? "none" : "flex";

    ensureGpsMap()?.update({
      sessionKey: `${data.vehicle_id || ""}/${data.session_id || ""}/${trackMode}`,
      track,
      mission: waypoints,
      missionRevision: mission?.revision_key || mission?.payload_hash || `${mission?.revision || 0}:${mission?.generated_at || ""}`,
      guidedTarget,
      manualSegment,
      activeWaypointSeq: activeWaypoint,
      latest: lastValidPosition ? {
        ...lastValidPosition,
        state: lastValidPosition.state,
        speed: lastValidPosition.speed,
      } : null,
      freshness: view.playback ? "PLAYBACK" : gpsPositionCurrent ? "LIVE" : lastValidPosition ? "OFFLINE" : "NO_DATA",
    });

    const meta = data.track_meta || {};
    $("trackModeSummary").textContent = view.playback
      ? `Playback ${integer(track.length)} / ${integer(playback.points.length)} points`
      : trackMode === "recent"
        ? `Recent ${integer(meta.returned_points || track.length)} of ${integer(meta.total_valid_points || track.length)} valid records`
        : `Main Track · ${integer(meta.returned_points || track.length)} displayed from ${integer(meta.total_valid_points || track.length)}`;
  }

  function renderState(data) {
    const history = data.state_history || [];
    $("transitionCount").textContent = `${history.length} transitions`;
    $("transitionRows").innerHTML = history.slice().reverse().map((item) => `<tr>
      <td>${integer(item.seq_id)}</td><td>${escapeHtml(localTime(item.timestamp))}</td>
      <td>${item.from_state ? pill(item.from_state) : pill("START")}</td><td>${pill(item.to_state)}</td>
      <td>${escapeHtml(text(item.session_id))}</td>
    </tr>`).join("") || `<tr><td colspan="5" class="empty-cell">Belum ada transisi state.</td></tr>`;
    drawStateChart($("stateChart"), data.chart_records || []);
    drawBarChart($("distributionChart"), data.summary?.state_distribution || {}, STATE_COLORS);
  }

  function renderStoreForward(data) {
    const summary = data.summary || {};
    const rows = data.store_forward_records || [];
    $("bufferHealth").innerHTML = detailRows([
      ["Maximum buffer", integer(summary.max_buffer_count)],
      ["Final buffer", integer(summary.final_buffer_count)],
      ["LIVE records", integer(summary.live_records)],
      ["REPLAY records", integer(summary.replay_records)],
      ["Total retry count", integer(summary.retry_total)],
      ["Maximum retry / record", integer(summary.max_retry_count)],
      ["Final sync status", escapeHtml(text(summary.final_sync_status))],
      ["Recovery records", integer(summary.recovery_records)],
    ]);
    $("storeForwardCount").textContent = `${rows.length} displayed records`;
    $("storeForwardRows").innerHTML = rows.slice().reverse().map((item) => `<tr>
      <td>${integer(item.seq_id)}</td><td>${escapeHtml(localTime(item.timestamp))}</td><td>${pill(item.state)}</td>
      <td>${pill(item.delivery_type)}</td><td>${pill(item.ack_status)}</td><td>${integer(item.buffer_count)}</td>
      <td>${integer(item.retry_count)}</td><td>${pill(item.sync_status)}</td>
    </tr>`).join("") || `<tr><td colspan="8" class="empty-cell">Tidak ada record link-loss, replay, buffer, atau retry pada session ini.</td></tr>`;
    drawLineChart($("bufferChart"), data.chart_records || [], [
      {key: "buffer_count", label: "Buffer", color: "#0c7b79", fill: "rgba(12,123,121,.12)"},
      {key: "retry_count", label: "Retry", color: "#b76a0a"},
    ]);
  }

  function renderDelivery(data) {
    const summary = data.summary || {};
    $("deliverySummary").innerHTML = detailRows([
      ["Unique records stored", integer(summary.total_records)],
      ["Delivery receipts", integer(summary.delivery_receipts)],
      ["Inserted deliveries", integer(summary.inserted_deliveries)],
      ["Duplicate deliveries", integer(summary.duplicate_deliveries)],
      ["Application ACK sent", integer(summary.ack_send_count)],
      ["Average ACK latency", `${number(summary.average_ack_latency_ms, 2)} ms`],
      ["Maximum ACK latency", `${number(summary.max_ack_latency_ms, 2)} ms`],
      ["Missing unique sequence", integer(summary.missing_records)],
    ]);
    drawLineChart($("ackChart"), data.chart_records || [], [
      {key: "ack_latency_ms", label: "ACK latency (ms)", color: "#28638f", fill: "rgba(40,99,143,.10)"},
    ]);
    drawLineChart($("operationChart"), data.chart_records || [], [
      {key: "speed", label: "Speed (m/s)", color: "#0c7b79"},
      {key: "battery_v", label: "Battery (V)", color: "#b76a0a"},
    ], {normalizeSeries: true});
  }

  function renderEvaluation(data) {
    const summary = data.summary || {};
    const latest = data.latest || {};
    const items = [
      ["Session records", integer(summary.total_records), summary.total_records > 0 ? "AVAILABLE" : "NO DATA"],
      ["NORMAL records", integer(summary.normal_records), "Operational state evidence"],
      ["GCS_LOST records", integer(summary.gcs_lost_records), summary.gcs_lost_records > 0 ? "Link-loss captured" : "Not observed"],
      ["RECOVERY records", integer(summary.recovery_records), summary.recovery_records > 0 ? "Recovery captured" : "Not observed"],
      ["PIXHAWK_LOST records", integer(summary.pixhawk_lost_records), summary.pixhawk_lost_records > 0 ? "Pixhawk loss captured" : "Not observed"],
      ["Maximum onboard buffer", integer(summary.max_buffer_count), "Store-and-forward capacity used"],
      ["Replay records", integer(summary.replay_records), summary.replay_records > 0 ? "Buffered data resent" : "No replay"],
      ["Duplicate deliveries", integer(summary.duplicate_deliveries), "Not stored twice"],
      ["Missing sequence", integer(summary.missing_records), Number(summary.missing_records) === 0 ? "CONTINUOUS" : "GAPS DETECTED"],
      ["Final state", text(summary.final_state), "Last known state"],
      ["Final buffer", integer(summary.final_buffer_count), Number(summary.final_buffer_count) === 0 ? "EMPTY" : "PENDING"],
      ["Final sync", text(summary.final_sync_status), "Last known synchronization"],
      ["Data source", (summary.data_sources || []).join(" + ") || text(latest.data_source), "Verify simulation vs field data"],
    ];
    $("evaluationRows").innerHTML = items.map(([parameter, value, interpretation]) => `<tr><td>${escapeHtml(parameter)}</td><td><strong>${escapeHtml(value)}</strong></td><td>${escapeHtml(interpretation)}</td></tr>`).join("");

    const records = (data.chart_records || []).slice(-40).reverse();
    $("eventRows").innerHTML = records.map((item) => `<tr>
      <td>${integer(item.seq_id)}</td><td>${escapeHtml(localTime(item.timestamp))}</td><td>${pill(item.state)}</td>
      <td>${pill(networkLabel(item.internet_status), item.internet_status === "UNAVAILABLE" ? "down" : "live")}</td>
      <td>${pill(mqttLabel(item.mqtt_status), item.mqtt_status === "DISCONNECTED" ? "down" : "live")}</td>
      <td>${pill(item.delivery_type)}</td><td>${integer(item.buffer_count)}</td><td>${pill(item.sync_status)}</td>
    </tr>`).join("") || `<tr><td colspan="8" class="empty-cell">Belum ada record operasi.</td></tr>`;
  }

  function renderExport(data) {
    const button = $("exportButton");
    if (!data.vehicle_id || !data.session_id) {
      button.href = "#"; button.classList.add("disabled");
      $("exportTitle").textContent = "Pilih session untuk menyiapkan ekspor";
      return;
    }
    const filename = `telemetry_${data.vehicle_id}_${data.session_id}.csv`.replace(/[^A-Za-z0-9_.-]+/g, "_");
    button.href = `/api/export/session.csv?vehicle_id=${encodeURIComponent(data.vehicle_id)}&session_id=${encodeURIComponent(data.session_id)}`;
    button.download = filename;
    button.classList.remove("disabled");
    $("exportTitle").textContent = `${data.vehicle_id} · ${data.session_id}`;
    $("exportDescription").textContent = `${integer(data.summary?.total_records)} unique records akan diekspor. File tidak mengubah database server.`;
  }

  function setupCanvas(canvas) {
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    const viewport = canvas.closest(".chart-viewport") || canvas.parentElement;
    const rect = viewport.getBoundingClientRect();
    const width = Math.max(320, Math.floor(rect.width || viewport.clientWidth || 600));
    const height = Math.max(180, Math.floor(rect.height || viewport.clientHeight || 250));
    const pixelWidth = Math.round(width * ratio);
    const pixelHeight = Math.round(height * ratio);
    if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
    if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    return {ctx, width, height};
  }

  function drawChartFrame(ctx, width, height, min, max, labels = []) {
    const pad = {left: 48, right: 16, top: 28, bottom: 32};
    ctx.clearRect(0, 0, width, height);
    ctx.font = "10px system-ui";
    ctx.strokeStyle = "#dfe7ea";
    ctx.fillStyle = "#788b95";
    ctx.lineWidth = 1;
    for (let index = 0; index <= 4; index++) {
      const y = pad.top + index * (height - pad.top - pad.bottom) / 4;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke();
      const value = max - index * (max - min) / 4;
      ctx.fillText(Number.isFinite(value) ? value.toFixed(Math.abs(max-min) < 10 ? 1 : 0) : "", 5, y + 3);
    }
    labels.forEach((label, index) => {
      ctx.fillStyle = label.color;
      ctx.fillRect(pad.left + index * 120, 7, 12, 3);
      ctx.fillStyle = "#596d78";
      ctx.fillText(label.label, pad.left + 18 + index * 120, 11);
    });
    return pad;
  }

  function drawLineChart(canvas, records, series, options = {}) {
    const {ctx, width, height} = setupCanvas(canvas);
    if (!records.length) return drawNoData(ctx, width, height);
    const validSeries = series.map((item) => ({...item, values: records.map((record) => {
      const value = Number(record[item.key]); return Number.isFinite(value) ? value : null;
    })}));
    const rawValues = validSeries.flatMap((item) => item.values.filter((value) => value !== null));
    if (!rawValues.length) return drawNoData(ctx, width, height);
    let min = Math.min(...rawValues), max = Math.max(...rawValues);
    if (options.normalizeSeries) { min = 0; max = 100; }
    if (min === max) { min -= 1; max += 1; }
    const pad = drawChartFrame(ctx, width, height, min, max, validSeries);
    const xAt = (index) => pad.left + index * (width - pad.left - pad.right) / Math.max(1, records.length - 1);
    validSeries.forEach((item) => {
      const own = item.values.filter((value) => value !== null);
      const ownMin = Math.min(...own), ownMax = Math.max(...own);
      const yValue = (value) => {
        const plotted = options.normalizeSeries ? (ownMax === ownMin ? 50 : (value-ownMin)/(ownMax-ownMin)*100) : value;
        return height - pad.bottom - (plotted - min) / (max - min) * (height - pad.top - pad.bottom);
      };
      ctx.beginPath(); let started = false;
      item.values.forEach((value, index) => {
        if (value === null) { started = false; return; }
        const x = xAt(index), y = yValue(value);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = item.color; ctx.lineWidth = 2.2; ctx.stroke();
    });
    ctx.fillStyle = "#788b95";
    ctx.fillText(`seq ${text(records[0].seq_id)}`, pad.left, height - 10);
    ctx.textAlign = "right"; ctx.fillText(`seq ${text(records[records.length-1].seq_id)}`, width-pad.right, height-10); ctx.textAlign = "left";
    if (options.normalizeSeries) { ctx.fillStyle="#93a1a8"; ctx.fillText("Series normalized for trend comparison", pad.left, height-10); }
  }

  function drawStateChart(canvas, records) {
    const {ctx, width, height} = setupCanvas(canvas);
    if (!records.length) return drawNoData(ctx, width, height);
    const states = ["PIXHAWK_LOST", "RECOVERY", "GCS_LOST", "NORMAL"];
    const pad = {left: 95, right: 16, top: 22, bottom: 30};
    ctx.clearRect(0,0,width,height); ctx.font="10px system-ui";
    states.forEach((state, index) => {
      const y = pad.top + index * (height-pad.top-pad.bottom)/3;
      ctx.strokeStyle="#dfe7ea"; ctx.beginPath(); ctx.moveTo(pad.left,y); ctx.lineTo(width-pad.right,y); ctx.stroke();
      ctx.fillStyle=STATE_COLORS[state]; ctx.fillText(state, 5, y+3);
    });
    const yAt = (state) => pad.top + states.indexOf(state) * (height-pad.top-pad.bottom)/3;
    const xAt = (index) => pad.left + index * (width-pad.left-pad.right)/Math.max(1,records.length-1);
    ctx.beginPath();
    records.forEach((record,index) => { const x=xAt(index), y=yAt(record.state); if(index===0) ctx.moveTo(x,y); else { const previous=yAt(records[index-1].state); ctx.lineTo(x,previous); ctx.lineTo(x,y); }});
    ctx.strokeStyle="#173e4d"; ctx.lineWidth=2.5; ctx.stroke();
    records.forEach((record,index) => { if(index===0 || record.state!==records[index-1].state){ctx.fillStyle=STATE_COLORS[record.state]||"#687b86";ctx.beginPath();ctx.arc(xAt(index),yAt(record.state),4,0,Math.PI*2);ctx.fill();}});
    ctx.fillStyle="#788b95"; ctx.fillText(`seq ${text(records[0].seq_id)}`,pad.left,height-9);ctx.textAlign="right";ctx.fillText(`seq ${text(records.at(-1).seq_id)}`,width-pad.right,height-9);ctx.textAlign="left";
  }

  function drawBarChart(canvas, valuesObject, colors) {
    const {ctx,width,height}=setupCanvas(canvas);
    const entries=Object.entries(valuesObject); if(!entries.length) return drawNoData(ctx,width,height);
    const max=Math.max(1,...entries.map(([,value])=>Number(value)||0)); const pad={left:42,right:14,top:20,bottom:55};
    ctx.clearRect(0,0,width,height);ctx.font="10px system-ui";
    for(let i=0;i<=4;i++){const y=pad.top+i*(height-pad.top-pad.bottom)/4;ctx.strokeStyle="#dfe7ea";ctx.beginPath();ctx.moveTo(pad.left,y);ctx.lineTo(width-pad.right,y);ctx.stroke();ctx.fillStyle="#788b95";ctx.fillText(Math.round(max-i*max/4),5,y+3);}
    const slot=(width-pad.left-pad.right)/entries.length;
    entries.forEach(([label,value],index)=>{const v=Number(value)||0;const barWidth=Math.min(62,slot*.58);const x=pad.left+index*slot+(slot-barWidth)/2;const y=height-pad.bottom-v/max*(height-pad.top-pad.bottom);ctx.fillStyle=colors[label]||"#0c7b79";ctx.fillRect(x,y,barWidth,height-pad.bottom-y);ctx.fillStyle="#536975";ctx.textAlign="center";ctx.fillText(integer(v),x+barWidth/2,y-6);ctx.save();ctx.translate(x+barWidth/2,height-12);ctx.rotate(-.25);ctx.fillText(label,0,0);ctx.restore();});ctx.textAlign="left";
  }

  function drawNoData(ctx,width,height){ctx.clearRect(0,0,width,height);ctx.fillStyle="#82919a";ctx.font="12px system-ui";ctx.textAlign="center";ctx.fillText("No chart data",width/2,height/2);ctx.textAlign="left";}

  function drawSupportingPlaceholder(canvas, labels, accent = "#0c7b79") {
    const {ctx, width, height} = setupCanvas(canvas);
    const pad = {left: 46, right: 16, top: 44, bottom: 34};
    ctx.clearRect(0, 0, width, height);
    ctx.font = "9px system-ui";

    for (let index = 0; index <= 4; index++) {
      const y = pad.top + index * (height - pad.top - pad.bottom) / 4;
      ctx.strokeStyle = "#e1e9ec";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(width - pad.right, y);
      ctx.stroke();
      ctx.fillStyle = "#94a2a9";
      ctx.fillText("—", 17, y + 3);
    }

    const legendWidth = Math.max(84, Math.floor((width - pad.left - pad.right) / Math.max(1, labels.length)));
    labels.forEach((label, index) => {
      const x = pad.left + index * legendWidth;
      ctx.fillStyle = label.color || accent;
      ctx.fillRect(x, 13, 12, 3);
      ctx.fillStyle = "#60747e";
      ctx.fillText(label.label, x + 18, 17);
    });

    ctx.fillStyle = "#60747e";
    ctx.font = "900 17px system-ui";
    ctx.textAlign = "center";
    ctx.fillText("NO DATA", width / 2, height / 2 - 3);
    ctx.fillStyle = "#8a9aa2";
    ctx.font = "10px system-ui";
    ctx.fillText("Waiting for sensor data", width / 2, height / 2 + 18);
    ctx.fillText("Time →", width / 2, height - 10);
    ctx.textAlign = "left";
  }

  function setSensorStatus(id, value, hasData) {
    const element = $(id);
    element.textContent = value;
    element.className = `sensor-status-badge ${hasData ? "" : "no-data"}`.trim();
  }

  function setChartStatus(id, value, hasData, solar = false) {
    const element = $(id);
    element.textContent = value;
    element.className = `sensor-chart-status ${solar ? "solar" : ""} ${hasData ? "" : "no-data"}`.trim();
  }

  function sensorValue(value, digits = 2) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric.toFixed(digits) : "—";
  }

  function renderSupportingMonitoring(data) {
    const supporting = data.supporting_sensors || {};
    const records = Array.isArray(supporting.records) ? supporting.records : [];
    const latest = supporting.latest || {};
    const hasData = supporting.status === "DATA" && records.length > 0;
    const sourceLabel = hasData ? text(supporting.source || "DATABASE") : "NO DATA";

    setSensorStatus("waterSensorStatus", sourceLabel, hasData);
    setSensorStatus("solarSensorStatus", sourceLabel, hasData);
    $("waterSensorEmpty").style.display = hasData ? "none" : "flex";
    $("solarSensorEmpty").style.display = hasData ? "none" : "flex";

    $("waterTemperatureValue").textContent = sensorValue(latest.water_temperature_c, 2);
    $("waterPhValue").textContent = sensorValue(latest.water_ph, 2);
    $("waterTurbidityValue").textContent = sensorValue(latest.water_turbidity_ntu, 2);
    $("waterTdsValue").textContent = sensorValue(latest.water_tds_ppm, 1);
    $("waterDoValue").textContent = sensorValue(latest.water_dissolved_oxygen_mg_l, 2);
    $("waterConductivityValue").textContent = sensorValue(latest.water_conductivity_us_cm, 1);
    $("solarVoltageValue").textContent = sensorValue(latest.solar_pv_voltage_v, 2);
    $("solarCurrentValue").textContent = sensorValue(latest.solar_pv_current_a, 2);
    $("solarPowerValue").textContent = sensorValue(latest.solar_pv_power_w, 1);
    $("solarBatteryVoltageValue").textContent = sensorValue(latest.solar_battery_voltage_v, 2);
    $("solarChargeCurrentValue").textContent = sensorValue(latest.solar_charge_current_a, 2);
    $("solarSocValue").textContent = sensorValue(latest.solar_state_of_charge_pct, 1);

    ["waterTempPhStatus", "waterChemistryStatus", "solarPvStatus", "solarBatteryStatus"].forEach((id, index) => {
      setChartStatus(id, sourceLabel, hasData, index >= 2);
    });

    if (!hasData) {
      drawSupportingPlaceholder($("waterTempPhChart"), [
        {label: "Temperature (°C)", color: "#0c7b79"},
        {label: "pH", color: "#5b75b8"},
      ]);
      drawSupportingPlaceholder($("waterChemistryChart"), [
        {label: "Turbidity", color: "#b76a0a"},
        {label: "TDS", color: "#6d8e3b"},
        {label: "DO", color: "#4d83a8"},
        {label: "Conductivity", color: "#7c4d96"},
      ]);
      drawSupportingPlaceholder($("solarPvChart"), [
        {label: "PV Voltage", color: "#c98213"},
        {label: "PV Current", color: "#5f8d44"},
        {label: "PV Power", color: "#a8533f"},
      ], "#c98213");
      drawSupportingPlaceholder($("solarBatteryChart"), [
        {label: "Battery V", color: "#bc7a13"},
        {label: "Charge A", color: "#387f74"},
        {label: "SOC", color: "#5b75b8"},
      ], "#bc7a13");
      return;
    }

    drawLineChart($("waterTempPhChart"), records, [
      {key: "water_temperature_c", label: "Temperature", color: "#0c7b79"},
      {key: "water_ph", label: "pH", color: "#5b75b8"},
    ], {normalizeSeries: true});
    drawLineChart($("waterChemistryChart"), records, [
      {key: "water_turbidity_ntu", label: "Turbidity", color: "#b76a0a"},
      {key: "water_tds_ppm", label: "TDS", color: "#6d8e3b"},
      {key: "water_dissolved_oxygen_mg_l", label: "DO", color: "#4d83a8"},
      {key: "water_conductivity_us_cm", label: "Conductivity", color: "#7c4d96"},
    ], {normalizeSeries: true});
    drawLineChart($("solarPvChart"), records, [
      {key: "solar_pv_voltage_v", label: "PV Voltage", color: "#c98213"},
      {key: "solar_pv_current_a", label: "PV Current", color: "#5f8d44"},
      {key: "solar_pv_power_w", label: "PV Power", color: "#a8533f"},
    ], {normalizeSeries: true});
    drawLineChart($("solarBatteryChart"), records, [
      {key: "solar_battery_voltage_v", label: "Battery V", color: "#bc7a13"},
      {key: "solar_charge_current_a", label: "Charge A", color: "#387f74"},
      {key: "solar_state_of_charge_pct", label: "SOC", color: "#5b75b8"},
    ], {normalizeSeries: true});
  }


  function stopPlayback() {
    if (playbackTimer) window.clearInterval(playbackTimer);
    playbackTimer = null;
    playback.playing = false;
    $("playbackPlayButton").textContent = "Play";
  }

  function resetPlaybackState() {
    stopPlayback();
    playback.sessionKey = "";
    playback.points = [];
    playback.cursor = 0;
    playback.loading = false;
    $("playbackSlider").min = "0";
    $("playbackSlider").max = "0";
    $("playbackSlider").value = "0";
    $("playbackReadout").textContent = "Playback data belum dimuat.";
  }

  function renderPlaybackReadout() {
    if (!playback.points.length) {
      $("playbackReadout").textContent = playback.loading ? "Loading playback…" : "Playback data tidak tersedia.";
      return;
    }
    const point = playback.points[Math.min(playback.cursor, playback.points.length - 1)];
    $("playbackReadout").textContent = [
      `${playback.cursor + 1}/${playback.points.length}`,
      localTime(point.timestamp),
      `seq ${text(point.seq_id)}`,
      text(point.state),
      text(point.flight_mode),
      `WP ${text(point.wp_index, "0")}/${text(point.wp_total, "0")}`,
      `${number(point.speed, 1)} m/s`,
      `${number(point.battery_v, 1)} V`,
    ].join(" · ");
  }

  function renderPlaybackFrame() {
    $("playbackSlider").value = String(playback.cursor);
    renderPlaybackReadout();
    if (currentData) renderMission(currentData);
  }

  async function loadPlayback({force = false} = {}) {
    if (!currentData?.vehicle_id || !currentData?.session_id || playback.loading) return;
    const sessionKey = `${currentData.vehicle_id}/${currentData.session_id}`;
    if (!force && playback.sessionKey === sessionKey && playback.points.length) return;
    stopPlayback();
    playback.loading = true;
    playback.sessionKey = sessionKey;
    playback.points = [];
    playback.cursor = 0;
    renderPlaybackReadout();
    try {
      const params = new URLSearchParams({
        vehicle_id: currentData.vehicle_id,
        session_id: currentData.session_id,
        limit: "10000",
      });
      const response = await fetch(`/api/track/playback?${params.toString()}`, {cache: "no-store"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      playback.points = Array.isArray(payload.points) ? payload.points : [];
      playback.cursor = Math.max(0, playback.points.length - 1);
      $("playbackSlider").max = String(Math.max(0, playback.points.length - 1));
      $("playbackSlider").value = String(playback.cursor);
    } catch (error) {
      console.error("Playback gagal dimuat", error);
      playback.points = [];
      $("playbackReadout").textContent = "Playback gagal dimuat dari server.";
    } finally {
      playback.loading = false;
      renderPlaybackFrame();
    }
  }

  function startPlayback() {
    if (!playback.points.length) return;
    stopPlayback();
    if (playback.cursor >= playback.points.length - 1) playback.cursor = 0;
    playback.playing = true;
    $("playbackPlayButton").textContent = "Pause";
    playbackTimer = window.setInterval(() => {
      if (!playback.playing) return;
      const step = Math.max(1, Number(playback.speed || 1));
      playback.cursor = Math.min(playback.points.length - 1, playback.cursor + step);
      renderPlaybackFrame();
      if (playback.cursor >= playback.points.length - 1) stopPlayback();
    }, 500);
  }

  function setTrackMode(mode) {
    const normalized = ["main", "recent", "playback"].includes(mode) ? mode : "main";
    if (trackMode === normalized && normalized !== "playback") return;
    trackMode = normalized;
    document.querySelectorAll("[data-track-mode]").forEach((button) => {
      const active = button.dataset.trackMode === trackMode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    $("recentLimitControl").classList.toggle("hidden", trackMode !== "recent");
    $("playbackControls").classList.toggle("hidden", trackMode !== "playback");
    if (trackMode === "playback") {
      loadPlayback();
    } else {
      stopPlayback();
      refresh();
    }
  }


  function renderAll(data) {
    data.freshness = clientFreshness(data);
    const previousSessionKey = currentData ? `${currentData.vehicle_id}/${currentData.session_id}` : "";
    currentData = data;
    selectedVehicle = data.vehicle_id || selectedVehicle;
    selectedSession = data.session_id || selectedSession;
    const currentSessionKey = `${selectedVehicle}/${selectedSession}`;
    if (previousSessionKey && previousSessionKey !== currentSessionKey) resetPlaybackState();
    updateSelect($("vehicleSelect"), data.vehicles || [], selectedVehicle, (item) => item);
    updateSelect($("sessionSelect"), data.sessions || [], selectedSession, sessionLabel);
    const selectedMeta = (data.sessions || []).find((item) => item.session_id === selectedSession);
    $("sessionSelectionMeta").textContent = selectedMeta
      ? `[${String(selectedMeta.rank || 0).padStart(2, "0")}] ${selectedMeta.is_latest ? "LATEST SESSION" : "HISTORICAL SESSION"} · ID ${selectedSession}`
      : "Newest session is numbered [01]";
    renderOverview(data); renderNotice(data); renderMission(data); renderState(data); renderStoreForward(data); renderDelivery(data); renderSupportingMonitoring(data); renderEvaluation(data); renderExport(data);
    if (trackMode === "playback" && playback.sessionKey !== currentSessionKey) loadPlayback();
  }

  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const params = new URLSearchParams();
      if (selectedVehicle) params.set("vehicle_id", selectedVehicle);
      if (selectedSession) params.set("session_id", selectedSession);
      params.set("track_mode", trackMode === "recent" ? "recent" : "main");
      params.set("track_limit", String(trackMode === "recent" ? recentTrackLimit : 500));
      const response = await fetch(`/api/dashboard?${params.toString()}`, {cache: "no-store"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      renderAll(await response.json());
    } catch (error) {
      console.error(error); setFreshness(null, false);
      $("noticeBar").textContent = "Dashboard tidak dapat membaca API server. Periksa server.app dan koneksi Tailscale.";
      $("noticeBar").classList.remove("hidden");
    } finally { loading = false; }
  }

  $("mapZoomIn").addEventListener("click", () => gpsMap?.zoomIn());
  $("mapZoomOut").addEventListener("click", () => gpsMap?.zoomOut());
  $("mapFitButton").addEventListener("click", () => { gpsMap?.setFollow(false); gpsMap?.fitMission(); });
  $("mapFitTrackButton").addEventListener("click", () => { gpsMap?.setFollow(false); gpsMap?.fitTrack(); });
  $("mapCenterButton").addEventListener("click", () => gpsMap?.centerCurrent());
  $("mapFollowButton").addEventListener("click", (event) => {
    const enabled = gpsMap?.setFollow(event.currentTarget.getAttribute("aria-pressed") !== "true") ?? false;
    event.currentTarget.setAttribute("aria-pressed", String(enabled));
    event.currentTarget.textContent = enabled ? "Follow ON" : "Follow OFF";
    event.currentTarget.classList.toggle("active", enabled);
  });
  $("mapMissionToggle").addEventListener("change", (event) => gpsMap?.setLayerVisibility({mission: event.target.checked}));
  $("mapTrackToggle").addEventListener("change", (event) => gpsMap?.setLayerVisibility({track: event.target.checked}));
  $("mapManualToggle").addEventListener("change", (event) => gpsMap?.setLayerVisibility({manual: event.target.checked}));
  $("mapGuidedToggle").addEventListener("change", (event) => gpsMap?.setLayerVisibility({guided: event.target.checked}));

  document.querySelectorAll("[data-track-mode]").forEach((button) => {
    button.addEventListener("click", () => setTrackMode(button.dataset.trackMode));
  });
  $("trackLimitSelect").addEventListener("change", (event) => {
    recentTrackLimit = Number(event.target.value || 500);
    if (trackMode === "recent") refresh();
  });
  $("playbackSlider").addEventListener("input", (event) => {
    stopPlayback();
    playback.cursor = Math.min(Math.max(0, Number(event.target.value || 0)), Math.max(0, playback.points.length - 1));
    renderPlaybackFrame();
  });
  $("playbackPlayButton").addEventListener("click", () => {
    if (playback.playing) stopPlayback(); else startPlayback();
  });
  $("playbackResetButton").addEventListener("click", () => {
    stopPlayback();
    playback.cursor = 0;
    renderPlaybackFrame();
  });
  $("playbackSpeedSelect").addEventListener("change", (event) => {
    playback.speed = Number(event.target.value || 1);
  });

  $("vehicleSelect").addEventListener("change", (event) => { selectedVehicle=event.target.value;selectedSession="";refresh(); });
  $("sessionSelect").addEventListener("change", (event) => { selectedSession=event.target.value;resetPlaybackState();refresh(); });
  document.querySelectorAll(".side-nav a").forEach((link) => link.addEventListener("click", () => {document.querySelectorAll(".side-nav a").forEach((item)=>item.classList.remove("active"));link.classList.add("active");}));
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      if (currentData) {
        renderState(currentData);
        renderStoreForward(currentData);
        renderDelivery(currentData);
        renderSupportingMonitoring(currentData);
        gpsMap?.render(true);
      }
    }, 120);
  });

  refresh();
  setInterval(refresh, refreshMs);
})();
