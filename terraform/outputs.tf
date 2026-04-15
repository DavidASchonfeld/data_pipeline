# Outputs printed after terraform apply — useful for verifying resources and updating local configs.

output "instance_id" {
  description = "ASG ID — use to look up the current spot instance in the AWS Console"
  value       = aws_autoscaling_group.pipeline.id
}

output "elastic_ip" {
  description = "Static public IP — update ~/.ssh/config HostName if the EIP is ever rebuilt"
  value       = aws_eip.pipeline_eip.public_ip
}

output "ecr_repository_url" {
  description = "Full ECR image URL — the base goes into .env.deploy as ECR_REGISTRY"
  value       = aws_ecr_repository.flask_app.repository_url
}

output "ami_used" {
  description = "Ubuntu 24.04 AMI ID that Terraform selected — record this for disaster recovery notes"
  value       = data.aws_ami.ubuntu_24_04.id
}

output "ssh_connect_command" {
  description = "SSH command to connect to the instance (or just: ssh ec2-stock)"
  value       = "ssh -i ~/path/to/${var.key_pair_name}.pem ubuntu@${aws_eip.pipeline_eip.public_ip}"
}

output "dashboard_url" {
  description = "CloudFront URL — share this with visitors instead of the raw EIP"
  value       = "https://${aws_cloudfront_distribution.dashboard.domain_name}"
}
