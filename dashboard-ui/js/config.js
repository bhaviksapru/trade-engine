// config.js - update these values after deploy
// Run: sam deploy then copy outputs from stack outputs
// Paste the values below

window.CONFIG = {
  // From sam deploy --guided (check stack outputs): api_gateway_url
  apiUrl: "https://XXXX.execute-api.us-east-2.amazonaws.com/prod",

  // WebSocket endpoint - connects directly to the ALB (API Gateway does not
  // support WebSocket upgrades for ALB-backed HTTP API integrations).
  // Format: wss://<alb-dns>:8080/live
  //   Port 8080 is the AlbHttpDirectListener defined in compute.yaml.
  // The ALB DNS changes each day when alb_manager recreates it - update this
  // after each market-open cycle, or add a Route53 alias for a stable name.
  wsUrl: "wss://XXXX.elb.us-east-2.amazonaws.com:8080/live",

  // From sam deploy --guided (check stack outputs): dashboard_url
  dashboardUrl: "https://XXXX.cloudfront.net",

  // From sam deploy --guided (check stack outputs): cognito_hosted_ui (domain only, no path)
  // e.g. "https://trade-engine-XXXX.auth.us-east-2.amazoncognito.com"
  cognitoDomain: "https://trade-engine-XXXX.auth.us-east-2.amazoncognito.com",

  // From sam deploy --guided (check stack outputs): cognito_client_id
  cognitoClientId: "XXXX",

  // OAuth scopes - do not change
  cognitoScopes: "email openid",
};
