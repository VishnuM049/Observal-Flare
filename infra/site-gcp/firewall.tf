resource "google_compute_firewall" "site_http" {
  name    = "flare-site-${var.site_name}-http"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["flare-site"]
}

resource "google_compute_firewall" "site_iap_ssh" {
  name    = "flare-site-${var.site_name}-iap-ssh"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  # IAP tunnel IP range — only Google's IAP service can SSH in
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["flare-site"]
}
