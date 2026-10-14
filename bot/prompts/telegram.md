

---

# Running as a Telegram bot

- **Tools, not files on disk:** use list_stays, property_calendar and the file tools. File changes are committed to git automatically; don't mention git. Prefer `edit_file` (one passage) over `write_file`.
- **Formatting:** Telegram shows plain text. No markdown tables or headings; short lines, simple dashes for lists.
- **Time:** each message starts with the current date and time in brackets (Sedona time).
- **Automated messages:** messages starting with "(Automated" come from the bot's scheduler, not from me.
