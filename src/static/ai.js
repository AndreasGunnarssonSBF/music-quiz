// AI-hjälp utan konto: appen skriver en instruktion som kopieras till Claude eller ChatGPT,
// och AI:ns svar (JSON) klistras tillbaka. Ingen nyckel, ingen kostnad, inget skickas från appen.
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TYPE_SV = { trivia: "textfrågor", video: "låtar", image: "skivomslag" };

  // --- Instruktion för ett helt quiz -------------------------------------------------
  function quizPrompt(o) {
    const types = o.types.length ? o.types : ["trivia"];
    const plan = Array.from({ length: o.rounds }, (_, i) => types[i % types.length]);
    return `Du är en erfaren quizmakare. Skapa ett musikquiz på svenska.

TEMA: ${o.theme}
PUBLIK: ${o.audience || "vuxna kollegor på en fest, blandade åldrar"}
SVÅRIGHETSGRAD: ${o.level}
RUNDOR: ${o.rounds} st med ${o.perRound} frågor var, i den här ordningen: ${plan.map((t, i) => `runda ${i + 1} = ${TYPE_SV[t]}`).join(", ")}.
${o.extra ? `ÖNSKEMÅL: ${o.extra}\n` : ""}
Rundtyperna:
- "trivia" (textfrågor): en fråga i text med ett rätt svar.
- "video" (låtar): lagen hör en låt och ska gissa ARTISTEN. Välj kända låtar som är lätta att hitta som musikvideo. Quizmastern lägger själv in ljud/video.
- "image" (skivomslag): lagen ser ett skivomslag och ska gissa ARTISTEN. Välj kända album med igenkännbara omslag. Quizmastern lägger själv in bilden.

Krav:
- Fakta måste vara korrekta. Hitta inte på årtal, placeringar eller citat – välj hellre något du är säker på.
- Varje fråga har exakt 3 felaktiga alternativ som är trovärdiga: samma genre, era och typ som rätt svar (artister mot artister, årtal mot årtal), aldrig skämtsvar, aldrig ett alternativ som också kan vara rätt.
- Variera frågorna och undvik att samma artist förekommer flera gånger.
- Rundorna ska ha korta, roliga namn.
- "fact" är en kort, sann kuriositet (en–två meningar) som visas i facit.

Svara ENDAST med JSON i exakt det här formatet, i ett kodblock:
\`\`\`json
{
  "title": "Quizets namn",
  "rounds": [
    { "title": "Rundans namn", "type": "trivia", "instruction": "Svara på frågan",
      "questions": [ { "q": "Frågan?", "correct": "Rätt svar", "wrong": ["Fel 1", "Fel 2", "Fel 3"], "fact": "Kuriosa." } ] },
    { "title": "Rundans namn", "type": "video", "instruction": "Vem är artisten?",
      "questions": [ { "artist": "Artist", "song": "Låttitel", "year": 1984, "wrong": ["Artist 2", "Artist 3", "Artist 4"], "fact": "Kuriosa." } ] },
    { "title": "Rundans namn", "type": "image", "instruction": "Vilken artist gjorde albumet?",
      "questions": [ { "artist": "Artist", "album": "Albumtitel", "year": 1977, "wrong": ["Artist 2", "Artist 3", "Artist 4"] } ] }
  ]
}
\`\`\``;
  }

  // --- Instruktion för fel alternativ i en runda --------------------------------------
  function wrongPrompt(round) {
    const t = round.type || "trivia";
    const rows = (round.questions || []).map((q, i) => {
      const right = (q.options || [])[0] || "";
      if (t === "trivia") return `${i + 1}. Fråga: ${q.q || "?"} | Rätt svar: ${right}`;
      if (t === "video") return `${i + 1}. Låt: ${q.song || "?"} | Rätt artist: ${right}`;
      return `${i + 1}. Album: ${q.a || "?"} | Rätt artist: ${right}`;
    }).join("\n");
    return `Du hjälper till med ett musikquiz på svenska. Rundan heter "${round.title || ""}" (${TYPE_SV[t]}).
Hitta på exakt 3 felaktiga svarsalternativ till varje fråga nedan.

Krav: alternativen ska vara trovärdiga och av samma slag som rätt svar (artister mot artister från samma genre och era, årtal mot årtal), inga skämtsvar och inget alternativ som också kan vara rätt. Upprepa inte rätt svar.

${rows}

Svara ENDAST med JSON i ett kodblock, i samma ordning:
\`\`\`json
{ "questions": [ { "n": 1, "wrong": ["Fel 1", "Fel 2", "Fel 3"] } ] }
\`\`\``;
  }

  // --- Tolka svaret -------------------------------------------------------------------
  function parseJson(text) {
    let t = String(text || "").trim();
    const fence = t.match(/```(?:json)?\s*([\s\S]*?)```/i);
    if (fence) t = fence[1];
    const a = t.indexOf("{"), b = t.lastIndexOf("}");
    if (a < 0 || b < a) throw new Error("Hittar ingen JSON i svaret. Kopiera hela svaret från AI:n.");
    t = t.slice(a, b + 1);
    try { return JSON.parse(t); } catch (e) { /* försök laga vanliga fel nedan */ }
    // Avslutande kommatecken, och typografiska citattecken som AI:n ibland använder runt nycklar och värden.
    const fixed = t.replace(/,\s*([}\]])/g, "$1");
    try { return JSON.parse(fixed); } catch (e) { /* sista försöket */ }
    try { return JSON.parse(fixed.replace(/[“”]/g, '"')); }
    catch (e) { throw new Error("Svaret gick inte att läsa (" + e.message + "). Be AI:n svara med giltig JSON och försök igen."); }
  }
  const str = (v) => String(v ?? "").trim();
  const wrongs = (q) => (Array.isArray(q.wrong) ? q.wrong : Array.isArray(q.options) ? q.options.slice(1) : []).map(str).filter(Boolean).slice(0, 3);

  function toQuiz(json) {
    if (!json || !Array.isArray(json.rounds) || !json.rounds.length) throw new Error("Svaret innehåller inga rundor.");
    const rounds = json.rounds.map((r) => {
      const type = ["trivia", "video", "image"].includes(r.type) ? r.type : "trivia";
      const questions = (r.questions || []).map((q) => {
        const right = str(q.correct || q.artist || (Array.isArray(q.options) ? q.options[0] : ""));
        const out = { ai: true, options: [right, ...wrongs(q)] };
        while (out.options.length < 4) out.options.push("");
        if (q.fact) out.fact = str(q.fact);
        if (type === "trivia") out.q = str(q.q || q.question);
        if (type === "video") { if (q.song) out.song = str(q.song); if (q.year && !out.fact) out.fact = `Från ${q.year}.`; }
        if (type === "image" && q.album) out.a = `${right} – ${str(q.album)}${q.year ? ` (${q.year})` : ""}`;
        return out;
      }).filter((q) => q.options[0]);
      return { title: str(r.title), type, instruction: str(r.instruction), questions };
    });
    return { title: str(json.title) || "Quiz från AI", rounds };
  }

  // --- Dialogen -----------------------------------------------------------------------
  let dlg;
  function ensureDialog() {
    if (dlg) return dlg;
    dlg = document.createElement("dialog");
    dlg.className = "ai-dlg";
    document.body.appendChild(dlg);
    return dlg;
  }
  /**
   * open({ title, intro, formHtml, getPrompt(form) -> string, apply(json, form) -> Promise<string|void>, applyLabel })
   */
  function open(o) {
    const d = ensureDialog();
    d.innerHTML = `<button class="iconbtn ai-x" title="Stäng">✕</button>
      <h3>✨ ${esc(o.title)}</h3>
      ${o.intro ? `<p class="hint" style="margin-top:0">${o.intro}</p>` : ""}
      <form class="ai-form" onsubmit="return false">${o.formHtml || ""}</form>
      <ol class="ai-steps">
        <li><b>Kopiera instruktionen</b>
          <div class="row" style="margin-top:6px"><button class="btn sm" data-a="copy">Kopiera instruktionen</button>
          <a class="btn ghost sm" href="https://claude.ai/new" target="_blank" rel="noopener">Öppna Claude</a>
          <a class="btn ghost sm" href="https://chatgpt.com/" target="_blank" rel="noopener">Öppna ChatGPT</a></div>
          <details style="margin-top:6px"><summary class="hint" style="cursor:pointer">Visa instruktionen</summary><textarea class="t ai-prompt" rows="8" readonly></textarea></details></li>
        <li><b>Klistra in den</b> i en ny chatt hos Claude eller ChatGPT och vänta på svaret.</li>
        <li><b>Kopiera hela svaret</b> och klistra in det här:
          <textarea class="t ai-answer" rows="6" placeholder="Klistra in AI:ns svar här"></textarea>
          <p class="err ai-err"></p></li>
      </ol>
      <div class="row" style="justify-content:flex-end"><button class="btn ghost sm" data-a="close">Avbryt</button><button class="btn sm" data-a="apply">${esc(o.applyLabel || "Använd svaret")}</button></div>`;
    const form = d.querySelector(".ai-form");
    const promptBox = d.querySelector(".ai-prompt");
    const refresh = () => { try { promptBox.value = o.getPrompt(form); } catch (e) { promptBox.value = ""; } };
    form.addEventListener("input", refresh);
    refresh();
    const err = d.querySelector(".ai-err");
    d.querySelector(".ai-x").onclick = () => d.close();
    d.querySelector('[data-a="close"]').onclick = () => d.close();
    d.querySelector('[data-a="copy"]').onclick = async (e) => {
      if (!form.reportValidity()) return;
      refresh();
      try { await navigator.clipboard.writeText(promptBox.value); }
      catch (x) { d.querySelector("details").open = true; promptBox.select(); document.execCommand("copy"); }
      e.target.textContent = "Kopierad ✓";
      setTimeout(() => (e.target.textContent = "Kopiera instruktionen"), 2500);
    };
    d.querySelector('[data-a="apply"]').onclick = async (e) => {
      err.textContent = "";
      e.target.disabled = true;
      try {
        const json = parseJson(d.querySelector(".ai-answer").value);
        const msg = await o.apply(json, form);
        d.close();
        if (msg && window.toast) window.toast(msg);
      } catch (x) { err.textContent = x.message; }
      finally { e.target.disabled = false; }
    };
    d.showModal();
    const first = form.querySelector("input, textarea");
    if (first) first.focus();
  }

  window.AI = { quizPrompt, wrongPrompt, parseJson, toQuiz, open, esc };
})();
