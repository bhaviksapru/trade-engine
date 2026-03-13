// config.js — ✏️ UPDATE THESE VALUES after running terraform apply
// Run: cd infra && terraform output
// Then paste the output values below

window.CONFIG = {
  // From terraform output: api_gateway_url
  apiUrl: "https://XXXX.execute-api.us-east-2.amazonaws.com/prod",

  // From terraform output: dashboard_url
  dashboardUrl: "https://XXXX.cloudfront.net",

  // From terraform output: cognito_hosted_ui (domain only, no path)
  // e.g. "https://trade-engine-XXXX.auth.us-east-2.amazoncognito.com"
  cognitoDomain: "https://trade-engine-XXXX.auth.us-east-2.amazoncognito.com",

  // From terraform output: cognito_client_id
  cognitoClientId: "XXXX",

  // OAuth scopes — do not change
  cognitoScopes: "email openid",
};
