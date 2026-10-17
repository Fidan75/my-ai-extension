
document.getElementById('runBtn').addEventListener('click', async () => {
  const resultDiv = document.getElementById('result');
  resultDiv.innerText = "Думаю...";

  try {
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
      resultDiv.innerText = "Ошибка: сначала выделите текст на странице!";
      return;
    }

    const response = await fetch(
      "http://127.0.0.1:8000/summarize",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          text: selectedText
        })
      }
    );

    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Ошибка запроса к серверу"
      );
    }

    resultDiv.innerText = data.summary;

  } catch (error) {
    resultDiv.innerText =
      "Ошибка: " + error.message;
  }
});
