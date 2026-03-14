// config.js - update these values after deploy
// Run: sam deploy then copy outputs from stack outputs
// Paste the values below

window.CONFIG = {
  // From sam deploy --guided (check stack outputs): api_gateway_url
  apiUrl: "https://XXXX.execute-api.us-east-2.amazonaws.com/prod",

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
