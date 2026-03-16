// websocket.js - live WebSocket connection to FastAPI
// Receives real-time price + trade events, calls registered handlers

const LiveFeed = (() => {
  let ws       = null;
  let handlers = {};
  let pingTimer = null;

  function connect() {
    const token  = Auth.getToken();
    if (!token) return;

    // CONFIG.wsUrl must point to the ALB WebSocket endpoint.
    // API Gateway HTTP API does not support WebSocket upgrades for ALB-backed
    // integrations, so the browser connects directly to the ALB.
    //
    // Format: wss://<alb-dns>:8080/live
    //   - Port 8080 is the ALB direct HTTP listener defined in compute.yaml
    //   - The ALB is recreated daily by alb_manager, so update config.js wsUrl
    //     after each deploy (or use a stable Route53 alias if you add one).
    const wsUrl = window.CONFIG.wsUrl + `?token=${encodeURIComponent(token)}`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log("[LiveFeed] Connected");
      _trigger("connected", {});
      // Start keepalive ping
      pingTimer = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
      }, 25000);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "ping") return; // server keepalive
        _trigger(msg.type, msg.data || {});
        _trigger("any", msg); // catch-all handler
      } catch (e) {
        console.warn("[LiveFeed] Malformed message:", event.data);
      }
    };

    ws.onclose = (event) => {
      console.warn(`[LiveFeed] Disconnected (${event.code}). Reconnecting in 5s...`);
      clearInterval(pingTimer);
      _trigger("disconnected", { code: event.code });
      if (event.code !== 4001 && event.code !== 4003) {
        // Reconnect unless auth failure
        setTimeout(connect, 5000);
      }
    };

    ws.onerror = (e) => {
      console.error("[LiveFeed] Error:", e);
    };
  }

  function disconnect() {
    clearInterval(pingTimer);
    if (ws) ws.close();
    ws = null;
  }

  // Register event handler: on("PriceUpdate", fn), on("TradeStateChange", fn), on("any", fn)
  function on(eventType, fn) {
    if (!handlers[eventType]) handlers[eventType] = [];
    handlers[eventType].push(fn);
  }

  function off(eventType, fn) {
    if (!handlers[eventType]) return;
    handlers[eventType] = handlers[eventType].filter(h => h !== fn);
  }

  function _trigger(eventType, data) {
    (handlers[eventType] || []).forEach(fn => { try { fn(data); } catch(e) { console.error(e); } });
  }

  return { connect, disconnect, on, off };
})();
