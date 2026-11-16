/* SPDX-License-Identifier: Apache-2.0 */
// Google Picker (docs/05): Google Sheets only, one spreadsheet per pick (added to the
// connection's list; owner, 11 Nov 2026), drive.file scope. A pasted link opens
// the Picker preselected on that file, because a link alone grants no access.
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-picker]");
  if (!btn) return;
  const form = document.getElementById("picked-files");
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const r = await fetch(`/sources/${btn.dataset.sid}/picker-token`, {method: "POST", headers: {"X-CSRF-Token": csrf}});
  if (!r.ok) return;
  const {token} = await r.json();
  let pre = null;
  if (btn.dataset.paste !== undefined) {
    const m = (prompt("Paste the spreadsheet link") || "").match(/\/d\/([A-Za-z0-9_-]+)/);
    if (!m) return;
    pre = m[1];
  }
  gapi.load("picker", () => {
    const view = new google.picker.DocsView(google.picker.ViewId.SPREADSHEETS).setMimeTypes(btn.dataset.mime);
    if (pre) view.setFileIds(pre);
    new google.picker.PickerBuilder()
      .addView(view)
      .setOAuthToken(token).setDeveloperKey(form.dataset.apiKey).setAppId(form.dataset.appId)
      .setCallback((data) => {
        if (data.action !== google.picker.Action.PICKED) return;
        form.action = `/sources/${btn.dataset.sid}/files`;
        data.docs.filter((d) => d.mimeType === btn.dataset.mime).forEach((d) => {
          const i = document.createElement("input"); i.type = "hidden"; i.name = "file"; i.value = d.id; form.appendChild(i);
        });
        form.submit();
      }).build().setVisible(true);
  });
});
