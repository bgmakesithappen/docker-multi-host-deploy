terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

# Variables
variable "key_name" {
  description = "SSH key pair name"
  type        = string
  default     = "docker-deploy-key"
}

variable "instance_count" {
  description = "Number of target hosts"
  type        = number
  default     = 3
}

# SSH Key Pair
resource "tls_private_key" "ssh" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_key_pair" "deployer" {
  key_name   = var.key_name
  public_key = tls_private_key.ssh.public_key_openssh
}

# Save private key locally
resource "local_file" "private_key" {
  content         = tls_private_key.ssh.private_key_pem
  filename        = "${path.module}/deploy-key.pem"
  file_permission = "0600"
}

# Security Group
resource "aws_security_group" "docker_hosts" {
  name        = "docker-multi-host"
  description = "Security group for Docker deployment hosts"

  ingress {
    description = "SSH from my IP"
    from_port = 22
    to_port = 22
    protocol = "tcp"
    cidr_blocks = ["67.2.28.195/32"]
    }

  ingress { 
    description = "HTTP from anywhere"
    from_port = 80
    to_port = 80
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    }
  
  ingress {
    description = "HTTPS from anywhere"
    from_port = 443
    to_port = 443
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    }
  
  egress {
    description = "Allow all outbound"
    from_port   = 0   
    to_port     = 0
    protocol    = "-1"   
    cidr_blocks = ["0.0.0.0/0"]
    }

  tags = {
    Name    = "docker-multi-host"
    Project = "docker-deploy"
  }
}

# EC2 Instances
resource "aws_instance" "docker_host" {
  count = var.instance_count

  ami           = "ami-0030e4319cbf4dbf2" 
  instance_type = "t3.micro"
  
  # Spot instance configuration
  instance_market_options {
    market_type = "spot"
    spot_options {
      max_price          = "0.0104" 
      spot_instance_type = "one-time"
    }
  }

  key_name               = aws_key_pair.deployer.key_name
  vpc_security_group_ids = [aws_security_group.docker_hosts.id]

user_data = <<-EOF
              #!/bin/bash
              # Update system
              apt-get update
              
              # Install prerequisites
              apt-get install -y ca-certificates curl
              
              # Add Docker's GPG key
              install -m 0755 -d /etc/apt/keyrings
              curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
              chmod a+r /etc/apt/keyrings/docker.asc
              
              # Add Docker repository
              echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
              
              # Install Docker
              apt-get update
              apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
              
              # Add ubuntu user to docker group
              usermod -aG docker ubuntu
              
              # Enable and start Docker
              systemctl enable docker
              systemctl start docker
              EOF

  tags = {
    Name    = "docker-host-${count.index + 1}"
    Project = "docker-deploy"
  }
}

# Outputs
output "host_ips" {
  description = "Public IPs of Docker hosts"
  value       = aws_instance.docker_host[*].public_ip
}

output "ssh_command" {
  description = "SSH connection example"
  value       = "ssh -i terraform/deploy-key.pem ubuntu@${aws_instance.docker_host[0].public_ip}"
}
