// websocket.js — live WebSocket connection to FastAPI
// Receives real-time price + trade events, calls registered handlers

const LiveFeed = (() => {
  let ws       = null;
  let handlers = {};
  let pingTimer = null;

  function connect() {
    const token  = Auth.getToken();
    if (!token) return;

    // Convert HTTPS API Gateway URL to WSS ALB URL
    // WebSocket connects to ALB directly (API Gateway doesn't support WSS natively for ALB targets)
    const wsUrl = window.CONFIG.apiUrl
      .replace("https://", "wss://")
      .replace(/execute-api.*amazonaws\.com\/prod/, "")
      + `live?token=${encodeURIComponent(token)}`;

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
