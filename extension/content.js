// Content script - runs on LinkedIn pages

async function handleProfile() {
  // Check if we have a pending message
  const data = await chrome.storage.local.get(["pendingMessage", "pendingLeadId", "pendingName"]);

  if (!data.pendingMessage) return;

  const message = data.pendingMessage;
  const name = data.pendingName;

  // Clear the pending data
  await chrome.storage.local.remove(["pendingMessage", "pendingLeadId", "pendingName"]);

  console.log("LinkedIn Sender: Found pending message for", name);

  // Wait for page to fully load
  await new Promise(r => setTimeout(r, 2000));

  // Find and click the Message button
  let messageBtn = document.querySelector('button[aria-label*="Message"]');

  if (!messageBtn) {
    // Try finding by text content
    const buttons = document.querySelectorAll('button');
    for (const btn of buttons) {
      if (btn.textContent.trim() === 'Message') {
        messageBtn = btn;
        break;
      }
    }
  }

  if (!messageBtn) {
    // Try finding in the actions section
    const spans = document.querySelectorAll('button span');
    for (const span of spans) {
      if (span.textContent.trim() === 'Message') {
        messageBtn = span.closest('button');
        break;
      }
    }
  }

  if (messageBtn) {
    console.log("LinkedIn Sender: Clicking Message button");
    messageBtn.click();

    // Wait for modal to open
    await new Promise(r => setTimeout(r, 2000));

    // Find the message input
    let msgInput = document.querySelector('div.msg-form__contenteditable[contenteditable="true"]');

    if (!msgInput) {
      msgInput = document.querySelector('div[role="textbox"][contenteditable="true"]');
    }

    if (!msgInput) {
      // Try finding any contenteditable in the message form
      const form = document.querySelector('.msg-form');
      if (form) {
        msgInput = form.querySelector('div[contenteditable="true"]');
      }
    }

    if (msgInput) {
      console.log("LinkedIn Sender: Filling message");
      msgInput.focus();

      // Clear any existing content
      msgInput.innerHTML = '';

      // Insert the message as a paragraph
      const p = document.createElement('p');
      p.textContent = message;
      msgInput.appendChild(p);

      // Trigger input event so LinkedIn registers the change
      msgInput.dispatchEvent(new Event('input', { bubbles: true }));
      msgInput.dispatchEvent(new Event('change', { bubbles: true }));

      console.log("LinkedIn Sender: Message filled! Click Send when ready.");

      // Show a subtle notification
      showNotification("Message filled! Click Send when ready.");
    } else {
      console.log("LinkedIn Sender: Could not find message input");
      showNotification("Could not find message field. Try clicking Message manually.");
    }
  } else {
    console.log("LinkedIn Sender: Could not find Message button");
    showNotification("Could not find Message button. You may not be connected to this person.");
  }
}

function showNotification(text) {
  const div = document.createElement('div');
  div.style.cssText = `
    position: fixed;
    top: 20px;
    right: 20px;
    background: #0a66c2;
    color: white;
    padding: 15px 20px;
    border-radius: 8px;
    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 14px;
    z-index: 999999;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
  `;
  div.textContent = text;
  document.body.appendChild(div);

  setTimeout(() => div.remove(), 5000);
}

// Run when page loads
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', handleProfile);
} else {
  handleProfile();
}

// Also handle SPA navigation
let lastUrl = location.href;
new MutationObserver(() => {
  if (location.href !== lastUrl) {
    lastUrl = location.href;
    setTimeout(handleProfile, 1000);
  }
}).observe(document, { subtree: true, childList: true });
