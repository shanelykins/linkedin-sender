const API_KEY = "tk_live_oRvKUUExEtF0MToaoVYTzmnh7D3ygasHxsSTJVq-4HY";
const API_URL = "https://www.thoughtful.app/api/v1";

const TEMPLATES = {
  A: "Hey {name}, saw you work at {company} using Claude Code. Ran across a tool called WOZCODE that claims 80% on Terminal Bench 2.0 vs 69% for Claude Code alone. Also cuts costs 25-55%. Free tier, no card. Curious what your take is — would you try something like this?",
  B: "Hey {name}, noticed {company} is scaling eng and probably using AI coding tools. WOZCODE cuts Claude Code costs 25-55% across a whole team — one plugin install per dev, no workflow changes. For a 10-person team, that's real money.\n\nWorth a 15-minute look?",
  C: "Hey {name}, quick question: do you know roughly what your engineering team spends on Claude Code each month? I ask because there's a plugin called WOZCODE that cuts that cost 25-55% with literally no change to how your engineers work. Thought it might be worth a quick look."
};

function classify(pos) {
  if (!pos) return "A";
  const p = pos.toLowerCase();
  if (["cto","vp","director","head of","chief"].some(k => p.includes(k)) &&
      ["engineer","tech","software","data","ai"].some(t => p.includes(t))) return "B";
  if (["founder","ceo","owner"].some(k => p.includes(k)) &&
      !["engineer","tech","cto","software"].some(t => p.includes(t))) return "C";
  return "A";
}

async function fetchLeads() {
  const resp = await fetch(`${API_URL}/pages`, {
    headers: { "Authorization": `Bearer ${API_KEY}` }
  });
  const data = await resp.json();

  const leads = [];
  for (const page of data.pages || []) {
    const props = page.properties || {};
    if (props.stage === "Not Contacted" && (props.touch_count || 0) === 0 &&
        props.linkedin_url && props.first_name) {
      const seq = classify(props.position || "");
      leads.push({
        id: page.id,
        firstName: props.first_name,
        lastName: props.last_name || "",
        company: props.company || "",
        position: props.position || "",
        linkedinUrl: props.linkedin_url,
        sequence: seq,
        message: TEMPLATES[seq].replace("{name}", props.first_name).replace("{company}", props.company || "")
      });
      if (leads.length >= 20) break;
    }
  }
  return leads;
}

async function updateLead(id) {
  await fetch(`${API_URL}/pages/${id}`, {
    method: "PATCH",
    headers: {
      "Authorization": `Bearer ${API_KEY}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ properties: { stage: "Contacted", touch_count: 1 } })
  });
}

async function openLeadInLinkedIn(lead) {
  // Store the message for the content script to use
  await chrome.storage.local.set({
    pendingMessage: lead.message,
    pendingLeadId: lead.id,
    pendingName: `${lead.firstName} ${lead.lastName}`
  });

  // Open LinkedIn messaging with the profile
  const profileSlug = lead.linkedinUrl.split("/in/")[1]?.replace(/\/$/, "") || "";

  // Open new tab with the profile, content script will handle the rest
  chrome.tabs.create({
    url: lead.linkedinUrl
  });
}

function renderLeads(leads) {
  const content = document.getElementById("content");

  if (leads.length === 0) {
    content.innerHTML = '<div class="status">No leads found</div>';
    return;
  }

  // Get already opened leads
  chrome.storage.local.get(["openedLeads"], (data) => {
    const opened = new Set(data.openedLeads || []);

    let html = "";
    leads.forEach((lead, i) => {
      const isOpened = opened.has(lead.id);
      html += `
        <div class="lead ${isOpened ? 'sent' : ''}" data-index="${i}">
          <div class="lead-name">${lead.firstName} ${lead.lastName}</div>
          <div class="lead-company">${lead.position} @ ${lead.company}</div>
          <div class="lead-msg">${lead.message.substring(0, 60)}...</div>
          ${!isOpened ? '<button class="btn">Open & Fill Message</button>' : ''}
        </div>
      `;
    });

    html += '<button class="btn btn-confirm" id="confirmAll">Confirm All Sent → Update Thoughtful</button>';

    content.innerHTML = html;

    // Add click handlers
    document.querySelectorAll(".lead .btn").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const index = parseInt(btn.closest(".lead").dataset.index);
        const lead = leads[index];

        // Mark as opened
        const data = await chrome.storage.local.get(["openedLeads"]);
        const openedLeads = data.openedLeads || [];
        if (!openedLeads.includes(lead.id)) {
          openedLeads.push(lead.id);
          await chrome.storage.local.set({ openedLeads });
        }

        await openLeadInLinkedIn(lead);
        btn.closest(".lead").classList.add("sent");
        btn.remove();
      });
    });

    document.getElementById("confirmAll")?.addEventListener("click", async () => {
      const data = await chrome.storage.local.get(["openedLeads"]);
      const openedLeads = data.openedLeads || [];

      if (openedLeads.length === 0) {
        alert("No leads to confirm");
        return;
      }

      const btn = document.getElementById("confirmAll");
      btn.textContent = "Updating...";
      btn.disabled = true;

      for (const id of openedLeads) {
        await updateLead(id);
      }

      await chrome.storage.local.set({ openedLeads: [] });
      btn.textContent = `Updated ${openedLeads.length} leads!`;

      setTimeout(() => location.reload(), 1500);
    });
  });
}

// Init
fetchLeads().then(renderLeads).catch(err => {
  document.getElementById("content").innerHTML = `<div class="status">Error: ${err.message}</div>`;
});
