
const resultDiv = document.getElementById("result");
const authSection = document.getElementById("authSection");
const appSection = document.getElementById("appSection");
const emailInput = document.getElementById("email");
const passwordInput = document.getElementById("password");

const API_URL = "https://my-ai-extension-backend.onrender.com";

function showMessage(message) {
  resultDiv.textContent = message;
}

async function updateLoginState() {
  const token = await getAccessToken();

  authSection.hidden = Boolean(token);
  appSection.hidden = !token;

  if (token) {
    document.getElementById("accountInfo").textContent =
      "You are logged in.";
    showMessage("Select text on a webpage, then click Summarize Text.");
  }
}

document.getElementById("signupBtn").addEventListener("click", async () => {
  const email = emailInput.value.trim();
  const password = passwordInput.value;

  if (!email || !password) {
    showMessage("Please enter your email and password.");
    return;
  }

  if (password.length < 6) {
    showMessage("Your password must be at least 6 characters.");
    return;
  }

  showMessage("Creating your account...");

  try {
    const data = await signUp(email, password);

    if (!data.access_token) {
      showMessage("Account created! Check your email to confirm your account, then log in.");
      return;
    }

    await signIn(email, password);
    await updateLoginState();
  } catch (error) {
    showMessage("Sign-up error: " + error.message);
  }
});

document.getElementById("loginBtn").addEventListener("click", async () => {
  const email = emailInput.value.trim();
  const password = passwordInput.value;

  if (!email || !password) {
    showMessage("Please enter your email and password.");
    return;
  }

  showMessage("Logging in...");

  try {
    await signIn(email, password);
    await updateLoginState();
  } catch (error) {
    showMessage("Login error: " + error.message);
  }
});

document.getElementById("logoutBtn").addEventListener("click", async () => {
  try {
    await signOut();
    authSection.hidden = false;
    appSection.hidden = true;
    passwordInput.value = "";
    showMessage("You have logged out.");
  } catch (error) {
    showMessage("Logout error: " + error.message);
  }
});

document.getElementById("runBtn").addEventListener("click", async () => {
  showMessage("Думаю...");

  try {
    const token = await getAccessToken();

    if (!token) {
      authSection.hidden = false;
      appSection.hidden = true;
      showMessage("Please log in again.");
      return;
    }

    const [tab] = await chrome.tabs.query({
      active: true,
      currentWindow: true
    });

    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => window.getSelection().toString()
    });

    const selectedText = results[0]?.result;

    if (!selectedText || selectedText.trim() === "") {
      showMessage("Error: First select text on the webpage!");
      return;
    }

    const response = await fetch(`${API_URL}/summarize`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`
      },
      body: JSON.stringify({
        text: selectedText
      })
    });

    const data = await response.json();

    if (response.status === 401) {
      await signOut();
      authSection.hidden = false;
      appSection.hidden = true;
      showMessage("Your session expired. Please log in again.");
      return;
    }

    if (response.status === 429) {
      showMessage(
        "Daily free limit reached!\n\n" +
        (data.detail || "You have used your 10 free summaries for today.")
      );
      return;
    }

    if (!response.ok) {
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Request failed."
      );
    }

    showMessage(data.summary);
  } catch (error) {
    showMessage("Error: " + error.message);
  }
});

updateLoginState().catch((error) => {
  showMessage("Could not check login: " + error.message);
});
