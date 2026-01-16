#!/usr/bin/env python3
"""
Multi-host Docker deployment script
Automates deployment of Docker Compose services across multiple servers
"""

import sys
import paramiko
from pathlib import Path
from colorama import init, Fore, Style
import time

# Initialize colorama for cross-platform colored output
init(autoreset=True)

class DeploymentManager:
    def __init__(self, key_path, service_path):
        self.key_path = Path(key_path)
        self.service_path = Path(service_path)
        self.results = []
        
    def load_hosts(self, hosts_file):
        """Load host list from file"""
        with open(hosts_file, 'r') as f:
            hosts = [line.strip() for line in f if line.strip()]
            return hosts
    
    def connect_ssh(self, host, username='ubuntu'):
        """Establish SSH connection to host"""
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy)
            key = paramiko.RSAKey.from_private_key_file(str(self.key_path))
            client.connect(hostname=host, username=username, pkey=key, timeout=10)

            return client
        
        except Exception as e:
            print(f"{Fore.RED}✗ Connection failed to {host}: {e}")
            raise

    def deploy_to_host(self, host):
        """Deploy service to a single host"""
        print(f"\n{Fore.CYAN}{'='*60}")
        print(f"{Fore.CYAN}Deploying to {host}")
        print(f"{Fore.CYAN}{'='*60}")
        
        client = None
        try:
            # Step 1: Connect
            print(f"{Fore.YELLOW}→ Connecting to {host}...")
            client = self.connect_ssh(host)
            print(f"{Fore.GREEN}✓ Connected")
            
            # Step 2: Create remote directory
            remote_dir = '/home/ubuntu/nginx-service'
            print(f"{Fore.YELLOW}→ Creating directory {remote_dir}...")
            stdin, stdout, stderr = client.exec_command(f'mkdir -p {remote_dir}')
            stdout.channel.recv_exit_status()
            print(f"{Fore.GREEN}✓ Directory created")
            
            # Step 3: Copy files via SFTP
            print(f"{Fore.YELLOW}→ Copying files...")
            sftp = client.open_sftp()
            
            local_compose = self.service_path / 'docker-compose.yml'
            sftp.put(str(local_compose), f'{remote_dir}/docker-compose.yml')
            
            local_html = self.service_path / 'html'
            sftp.mkdir(f'{remote_dir}/html')
            for html_file in local_html.iterdir():
                if html_file.is_file():
                    sftp.put(str(html_file), f'{remote_dir}/html/{html_file.name}')
            
            sftp.close()
            print(f"{Fore.GREEN}✓ Files copied")
            
            # Step 4: Pull images
            print(f"{Fore.YELLOW}→ Pulling Docker images...")
            stdin, stdout, stderr = client.exec_command(
                f'cd {remote_dir} && docker compose pull'
            )
            stdout.channel.recv_exit_status()
            print(f"{Fore.GREEN}✓ Images pulled")
            
            # Step 5: Start containers
            print(f"{Fore.YELLOW}→ Starting containers...")
            stdin, stdout, stderr = client.exec_command(
                f'cd {remote_dir} && docker compose up -d'
            )
            stdout.channel.recv_exit_status()
            print(f"{Fore.GREEN}✓ Containers started")
            
            # Step 6: Verify deployment
            print(f"{Fore.YELLOW}→ Verifying deployment...")
            stdin, stdout, stderr = client.exec_command('docker ps --format "{{.Names}}: {{.Status}}"')
            stdout.channel.recv_exit_status()
            output = stdout.read().decode().strip()
            print(f"{Fore.CYAN}   Running containers:")
            for line in output.split('\n'):
                print(f"{Fore.CYAN}   - {line}")
            
            time.sleep(2)
            stdin, stdout, stderr = client.exec_command('curl -s -o /dev/null -w "%{http_code}" http://localhost')
            status_code = stdout.read().decode().strip()
            
            if status_code == '200':
                print(f"{Fore.GREEN}✓ HTTP health check passed (200 OK)")
                print(f"{Fore.GREEN}✓ Deployment successful!")
                print(f"{Fore.WHITE}   Access at: http://{host}")
            else:
                print(f"{Fore.YELLOW}⚠ HTTP returned {status_code}")
            
            result = {
                'host': host,
                'status': 'success',
                'message': f'Deployed successfully - http://{host}'
            }
            
        except Exception as e:
            print(f"{Fore.RED}✗ Deployment failed: {e}")
            result = {
                'host': host,
                'status': 'failed',
                'message': str(e)
            }
        
        finally:
            if client:
                client.close()
        
        self.results.append(result)
        return result

    
    def print_summary(self):
        """Print deployment summary"""
        print(f"\n{Fore.YELLOW}{'='*60}")
        print(f"{Fore.YELLOW}DEPLOYMENT SUMMARY")
        print(f"{Fore.YELLOW}{'='*60}\n")
        
        for result in self.results:
            if result['status'] == 'success':
                print(f"{Fore.GREEN}✓ {result['host']}: {result['message']}")
            else:
                print(f"{Fore.RED}✗ {result['host']}: {result['message']}")

def main():
    # Paths
    key_path = Path(__file__).parent.parent / 'terraform' / 'deploy-key.pem'
    service_path = Path(__file__).parent.parent / 'services' / 'nginx'
    hosts_file = Path(__file__).parent / 'hosts.txt'
    
    # Create deployment manager
    manager = DeploymentManager(key_path, service_path)
    
    # Load hosts
    hosts = manager.load_hosts(hosts_file)
    
    print(f"{Fore.MAGENTA}{'='*60}")
    print(f"{Fore.MAGENTA}Docker Multi-Host Deployment Script")
    print(f"{Fore.MAGENTA}{'='*60}")
    print(f"\n{Fore.WHITE}Deploying to {len(hosts)} hosts...")
    
    # Deploy to each host
    for host in hosts:
        manager.deploy_to_host(host)
    
    # Print summary
    manager.print_summary()

if __name__ == '__main__':
    main()
