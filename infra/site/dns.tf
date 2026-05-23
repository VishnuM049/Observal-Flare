resource "aws_route53_record" "site" {
  zone_id = var.route53_zone_id
  name    = "${var.site_name}.${var.base_domain}"
  type    = "A"
  ttl     = 300
  records = [aws_eip.site.public_ip]
}
