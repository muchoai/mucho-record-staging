/**
 * The Record → Publish now
 * Paste this into the sheet's script editor (Extensions → Apps Script) in
 * "Ai visibility sheet" (Mucho Google account). Kept here as the reference copy.
 *
 * It sends the Public tab to GitHub and starts the "Update lists from sheet" job,
 * which rebuilds the Awards, Talks and Media lists on The Record.
 *
 * Needs one private setting (Project Settings → Script properties):
 *   GITHUB_TOKEN = a fine-grained GitHub token for muchoai/mucho-record-staging only,
 *                  with "Actions: Read and write" and nothing else.
 * The token never goes in a cell, in the chat or in GitHub.
 */

const REPO = 'muchoai/mucho-record-staging';
const WORKFLOW = 'update-lists.yml';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('The Record')
    .addItem('Publish now', 'publishNow')
    .addToUi();
}

function publishNow() {
  const ui = SpreadsheetApp.getUi();
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) {
    ui.alert('Publishing is not set up yet: the GitHub key is missing. Ask Hannah.');
    return;
  }
  const sheet = SpreadsheetApp.getActive().getSheetByName('Public');
  if (!sheet) {
    ui.alert('The Public tab is missing. Ask Hannah.');
    return;
  }
  const rows = sheet.getDataRange().getDisplayValues();
  const csv = rows
    .map(function (r) {
      return r.map(function (c) { return '"' + String(c).replace(/"/g, '""') + '"'; }).join(',');
    })
    .join('\n');
  const b64 = Utilities.base64Encode(csv, Utilities.Charset.UTF_8);

  const res = UrlFetchApp.fetch(
    'https://api.github.com/repos/' + REPO + '/actions/workflows/' + WORKFLOW + '/dispatches',
    {
      method: 'post',
      contentType: 'application/json',
      headers: {
        Authorization: 'Bearer ' + token,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'
      },
      payload: JSON.stringify({ ref: 'main', inputs: { csv_b64: b64 } }),
      muteHttpExceptions: true
    }
  );

  if (res.getResponseCode() === 204) {
    ui.alert(
      'Sent. The Record will update in a few minutes.\n\n' +
      'To confirm, check the page or its corrections log. If a row has a problem ' +
      '(a year in the text, a broken link, an item twice), nothing changes and Hannah gets an email.'
    );
  } else {
    ui.alert('Publishing failed (' + res.getResponseCode() + '). Ask Hannah.\n\n' + res.getContentText().slice(0, 300));
  }
}
