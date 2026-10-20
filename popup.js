// Helper function to get or create a unique User ID stored locally in Chrome
async function getUserId() {
  let { userId } = await chrome.storage.local.get("userId");
  if (!userId) {
    userId = "user_" + Math.random().toString(36).substring(2, 11);
    await chrome.storage.local.set({ userId });
  }
  return userId;
}

document.getElementById('runBtn').addEventListener('click', async () => {
  const resultDiv = document.getElementById('result');
  resultDiv.innerText = "Думаю...";

  try {
    // 1. Get the current active browser tab
    const [tab] = await chrome.tabs.query({
      active: true,
      currentWindow: true
    });

    // 2. Extract selected text from the active web page
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => window.getSelection().toString()
    });

    const selectedText = results[0]?.result;

    if (!selectedText || selectedText.trim() === "") {
      resultDiv.innerText = "Ошибка: сначала выделите текст на странице!";
      return;
    }

    // 3. Get unique user ID
    const userId = await getUserId();

    // 4. Send request to your LIVE Render backend
    const response = await fetch(
      "https://my-ai-extension-backend.onrender.com/summarize",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          text: selectedText,
          user_id: userId
        })
      }
    );

    const data = await response.json();

    // 5. Handle Rate Limits (HTTP 429 - Free limit reached)
    if (response.status === 429) {
      resultDiv.innerHTML = `
        <p style="color: #d9534f; font-weight: bold;">Лимит исчерпан!</p>
        <p>${data.detail || "Вы использовали 10 бесплатных суммаризаций на сегодня."}</p>
        <button id="checkoutBtn" style="background-color: #28a745; color: white; border: none; padding: 8px 12px; border-radius: 4px; cursor: pointer;">
          Перейти на Pro ($4.99/мес)
        </button>
      `;

      // Attach click event to the dynamically generated Upgrade button
      document.getElementById('checkoutBtn').addEventListener('click', async () => {
        try {
          const checkoutResponse = await fetch("https://my-ai-extension-backend.onrender.com/create-checkout", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: userId })
          });
          
          const checkoutData = await checkoutResponse.json();
          if (checkoutData.checkout_url) {
            chrome.tabs.create({ url: checkoutData.checkout_url });
          } else {
            alert("Не удалось создать ссылку на оплату.");
          }
        } catch (err) {
          alert("Ошибка оплаты: " + err.message);
        }
      });

      return;
    }

    // 6. Handle other error codes
    if (!response.ok) {
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Ошибка запроса к серверу"
      );
    }

    // 7. Display the AI summary
    resultDiv.innerText = data.summary;

  } catch (error) {
    resultDiv.innerText = "Ошибка: " + error.message;
  }
});