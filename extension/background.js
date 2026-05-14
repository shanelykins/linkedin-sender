// Background service worker
// Handles any background tasks if needed

chrome.runtime.onInstalled.addListener(() => {
  console.log("LinkedIn Sender extension installed");
  // Clear any stale data
  chrome.storage.local.set({ openedLeads: [] });
});
