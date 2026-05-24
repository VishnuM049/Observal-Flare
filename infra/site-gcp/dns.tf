# DNS managed in Route53 (observal.io zone lives in AWS)
resource "aws_route53_record" "site" {
  zone_id = var.route53_zone_id
  name    = "${var.site_name}.${var.base_domain}"
  type    = "A"
  ttl     = 300
  records = [google_compute_address.site.address]
}
