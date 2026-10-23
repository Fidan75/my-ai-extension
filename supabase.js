
const SUPABASE_URL = "https://qjcnhwvrmierkoexurlk.supabase.co";

// Replace this with your Supabase Publishable key.
const SUPABASE_PUBLISHABLE_KEY = "sb_publishable_rGEdTRSC9ugHRKIK88S8Hg_atmXg_NW";

const SUPABASE_AUTH_URL = `${SUPABASE_URL}/auth/v1`;

async function signUp(email, password) {
  const response = await fetch(`${SUPABASE_AUTH_URL}/signup`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "apikey": SUPABASE_PUBLISHABLE_KEY
    },
    body: JSON.stringify({ email, password })
  });

  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.msg || data.message || data.error_description || "Sign-up failed.");
  }

  return data;
}

async function signIn(email, password) {
  const response = await fetch(
    `${SUPABASE_AUTH_URL}/token?grant_type=password`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_PUBLISHABLE_KEY
      },
      body: JSON.stringify({ email, password })
    }
  );

  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.msg || data.message || data.error_description || "Login failed.");
  }

  await chrome.storage.local.set({
    supabaseAccessToken: data.access_token,
    supabaseRefreshToken: data.refresh_token
  });

  return data;
}

async function getAccessToken() {
  const { supabaseAccessToken, supabaseRefreshToken } =
    await chrome.storage.local.get([
      "supabaseAccessToken",
      "supabaseRefreshToken"
    ]);

  if (!supabaseAccessToken) {
    return null;
  }

  // Check whether the saved token has expired.
  try {
    const parts = supabaseAccessToken.split(".");
    const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")));

    if (payload.exp && payload.exp * 1000 > Date.now() + 60000) {
      return supabaseAccessToken;
    }
  } catch (error) {
    // Try refreshing an unreadable or expired token.
  }

  if (!supabaseRefreshToken) {
    await chrome.storage.local.remove([
      "supabaseAccessToken",
      "supabaseRefreshToken"
    ]);
    return null;
  }

  const response = await fetch(`${SUPABASE_AUTH_URL}/token?grant_type=refresh_token`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "apikey": SUPABASE_PUBLISHABLE_KEY
    },
    body: JSON.stringify({ refresh_token: supabaseRefreshToken })
  });

  const data = await response.json();

  if (!response.ok || !data.access_token) {
    await chrome.storage.local.remove([
      "supabaseAccessToken",
      "supabaseRefreshToken"
    ]);
    return null;
  }

  await chrome.storage.local.set({
    supabaseAccessToken: data.access_token,
    supabaseRefreshToken: data.refresh_token
  });

  return data.access_token;
}

async function signOut() {
  const token = await getAccessToken();

  if (token) {
    await fetch(`${SUPABASE_AUTH_URL}/logout`, {
      method: "POST",
      headers: {
        "apikey": SUPABASE_PUBLISHABLE_KEY,
        "Authorization": `Bearer ${token}`
      }
    });
  }

  await chrome.storage.local.remove([
    "supabaseAccessToken",
    "supabaseRefreshToken"
  ]);
}
