resource "google_service_account" "site" {
  account_id   = "flare-site-${var.site_name}"
  display_name = "Flare site ${var.site_name}"
  project      = var.project
}

resource "google_project_iam_member" "site_log_writer" {
  project = var.project
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.site.email}"
}
