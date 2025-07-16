# Verifone P400 Desktop App

A Python 3 / PyQt5 application that drives a Verifone P400 terminal for Quick‑Sale transactions, administrative commands, and webhook integration.  It provides a full GUI admin interface, a minimal “listener” window with system‐tray support, and an embedded HTTP server for REST‑style calls.

---

## 🎯 Features

### 1. Admin GUI (`MainWindow`)
- **Connection Settings**  
  – IP address & TCP port  
  – Training vs. production mode  
- **Merchant Registration**  
  – Configure chain, store, lane, terminal ID, POS type  
  – “Register” button issues an ADMIN REGISTER command  
- **Key Management**  
  – Exchange Keys (DES3 key transport)  
  – View decrypted MAC key  
- **Quick Sale**  
  – Enter an amount and click **Quick Sale**  
  – Pops up a manual‑vs‑regular entry chooser  
  – Shows a spinner overlay (“מעבד תשלום…”) until completion  
- **Transaction Flow**  
  – PING → STATUS → START_TRAN → DISCOVERY → AUTHORIZE → FINISH_TRAN  
  – Automatic fallback: if AUTHORIZE never completes, issues REPORT → GET_TRAN_DETAILS  
  – Auto‑cancel on RESULT_CODE 2  
  – SHVA ID validation (configured vs. reported terminal ID)  
  – Support for credit terms & installment selection via a dialog (`CreditTermDlg`)  
- **Admin Commands**  
  – Buttons for STATUS and EOD (end‑of‑day)  
  – Free‑call XML sandbox:  
    • Build templates (START_TRAN, DISCOVERY, AUTHORIZE, FINISH_TRAN)  
    • Paste or edit arbitrary XML, sign it, and send  
- **Logs & Search**  
  – Two panes showing raw XML SENT and RECEIVED  
  – Find text across both panes with highlights  
  – Clear logs on demand  

### 2. Webhook Listener (`WebhookListenerWindow`)
- **Minimal Window**  
  – Displays “Webhook: Active” and a **Settings** button  
  – Password‑protected (default password: `kosherplay2015`)  
- **System Tray Integration**  
  – Tray icon with menu: Open window, Settings, Exit  
  – Hides on minimize/close but keeps listening  

### 3. Embedded HTTP Server (`WebhookServer`)
- **`http://localhost:8080/pay` endpoint**  
  – `GET /pay?amount=000000012345` (12‑digit minor units)  
  – `GET /pay?amount=123.45&type=01` (decimal shekels + transaction type)  
  – `wait=1` query param: blocks until FINISH_TRAN completes, then returns JSON receipt  
- **`/GET_TRAN_DETAILS` endpoint**  
  – `GET /GET_TRAN_DETAILS?wait=1` issues REPORT → GET_TRAN_DETAILS and returns the last transaction details as JSON  
- **`/receipt` endpoint**  
  – `GET /receipt.json` or `GET /receipt` returns the last JSON receipt  

---

## 📡 API Endpoints & Examples

### 1. Quick‑Sale via `/pay`
Requests a sale of **₪ 9.01** (minor units `000000000901`), waits for the receipt, and returns JSON:

```none
http://localhost:8080/pay?amount=000000000901&type=01&wait=1
