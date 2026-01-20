#!/usr/bin/env python3
"""
Multi-host Docker deployment script
Automates deployment of Docker Compose services across multiple servers
"""

from http import client
import sys
#from flask import config  #obselete import post config yaml usage; leaving for reference and testing
import paramiko
from pathlib import Path
from colorama import init, Fore, Style
import time
import logging
from datetime import datetime
import yaml

# Initialize colorama for cross-platform colored output
init(autoreset=True)

log_dir = Path(__file__).parent / 'logs'
log_dir.mkdir(exist_ok=True)

# Setup logging with timestamp in filename
log_file = log_dir / f'deployment_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()  
    ]
)

logger = logging.getLogger(__name__)
logger.info(f"Logging to: {log_file}")

def load_config(config_path='config.yaml'):
    """Load configuration from YAML file"""
    config_file = Path(__file__).parent.parent / config_path
    
    if not config_file.exists():
        logger.warning(f"Config file not found at {config_file}, using defaults")
        return {
            'deployment': {
                'timeout': 300,
                'health_check_retries': 3,
                'retry_delay': 5,
                'remote_dir': '/home/ubuntu/nginx-service'
            },
            'services': [{
                'name': 'nginx',
                'path': 'services/nginx',
                'port': 80,
                'health_endpoint': '/'
            }],
            'logging': {'level': 'INFO'},
            'options': {
                'create_backup': True,
                'rollback_on_failure': False
            }
        }
    
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
    
    logger.info(f"Loaded configuration from {config_file}")
    return config

class DeploymentManager:
    def __init__(self, key_path, service_path, config, dry_run=False):
        self.key_path = Path(key_path)
        self.service_path = Path(service_path)
        self.config = config
        self.results = []
        # self.dry_run = dry_run ## Not used currently but can be implemented for future features  
        self.timeout = config['deployment']['timeout']
        self.remote_dir = config['deployment']['remote_dir']
        self.health_retries = config['deployment']['health_check_retries']
        self.retry_delay = config['deployment']['retry_delay']
        self.create_backup = config['options']['create_backup']

        if dry_run:
            print(f"{Fore.YELLOW}⚠ DRY RUN MODE - No changes will be made")
            logger.info("Running in DRY RUN mode")
    
        logger.info(f"Initialized DeploymentManager with timeout={self.timeout}s")        
    
    def load_hosts(self, hosts_file):
        """Load host list from file"""
        with open(hosts_file, 'r') as f:
            hosts = [line.strip() for line in f if line.strip()]
            return hosts
    
    def connect_ssh(self, host, username='ubuntu'):
        """Establish SSH connection to host"""
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            key = paramiko.RSAKey.from_private_key_file(str(self.key_path))
            client.connect(hostname=host, username=username, pkey=key, timeout=self.timeout)

            return client
        
        except Exception as e:
            print(f"{Fore.RED}✗ Connection failed to {host}: {e}")
            raise

    def deploy_to_host(self, host):
        """
        Deploy service to a single host via SSH.
        
        Performs the following steps:
        1. Establishes SSH connection
        2. Creates remote directory structure
        3. Backs up existing deployment (if enabled)
        4. Transfers files via SFTP
        5. Pulls Docker images
        6. Starts containers
        7. Performs health checks with retries
        
        Args:
            host (str): IP address or hostname of target server
            
        Returns:
            dict: Deployment result with keys: host, status, message
            
        Raises:
            Exception: If deployment fails (caught and returned as failed result)
        """

        logger.info(f"=== Starting deployment to {host} ===")

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
            remote_dir = self.remote_dir
            print(f"{Fore.YELLOW}→ Creating directory {remote_dir}...")
            logger.debug(f"Creating remote directory: {remote_dir}")
            stdin, stdout, stderr = client.exec_command(f'mkdir -p {remote_dir}')
            stdout.channel.recv_exit_status()
            print(f"{Fore.GREEN}✓ Directory created")
            
            # Step 3: Backup existing deployment
            if self.create_backup:
                print(f"{Fore.YELLOW}→ Backing up existing deployment (if any)...")
                logger.info("Creating backup of existing deployment") 
                stdin, stdout, stderr = client.exec_command(
                    f'if [ -d {remote_dir} ]; then '
                    f'rm -rf {remote_dir}.backup && '
                    f'cp -r {remote_dir} {remote_dir}.backup; fi'
                )
                stdout.channel.recv_exit_status()
                print(f"{Fore.GREEN}✓ Backup created")
            else:
                logger.info("Backup disabled in config, skipping")

            # Step 4: Copy files via SFTP
            print(f"{Fore.YELLOW}→ Copying files...")
            logger.info(f"Copying files from {self.service_path} to {host}:{remote_dir}")  
            sftp = client.open_sftp()

            local_compose = self.service_path / 'docker-compose.yml'
            sftp.put(str(local_compose), f'{remote_dir}/docker-compose.yml')
            
            local_html = self.service_path / 'html'
            try:
                sftp.mkdir(f'{remote_dir}/html')
            except:
                pass 
            for html_file in local_html.iterdir():
                if html_file.is_file():
                    sftp.put(str(html_file), f'{remote_dir}/html/{html_file.name}')
            
            sftp.close()
            print(f"{Fore.GREEN}✓ Files copied")
            
            # Step 5: Pull images
            print(f"{Fore.YELLOW}→ Pulling Docker images...")
            stdin, stdout, stderr = client.exec_command(
                f'cd {remote_dir} && docker compose pull'
            )
            stdout.channel.recv_exit_status()
            print(f"{Fore.GREEN}✓ Images pulled")
            
            # Step 6: Start containers
            print(f"{Fore.YELLOW}→ Starting containers...")
            stdin, stdout, stderr = client.exec_command(
                f'cd {remote_dir} && docker compose up -d'
            )
            stdout.channel.recv_exit_status()
            print(f"{Fore.GREEN}✓ Containers started")
            logger.info(f"Containers started successfully on {host}")  

            # Show running containers
            print(f"{Fore.YELLOW}→ Checking container status...")
            stdin, stdout, stderr = client.exec_command('docker ps --format "{{.Names}}: {{.Status}}"')
            stdout.channel.recv_exit_status()
            output = stdout.read().decode().strip()
            print(f"{Fore.CYAN}   Running containers:")
            for line in output.split('\n'):
                if line:  # Only print non-empty lines
                    print(f"{Fore.CYAN}   - {line}")

            # Step 7: Verify deployment (Health Check)
            for attempt in range(self.health_retries):
                time.sleep(self.retry_delay)
                stdin, stdout, stderr = client.exec_command('curl -s -o /dev/null -w "%{http_code}" http://localhost')
                status_code = stdout.read().decode().strip()
                
                if status_code == '200':
                    print(f"{Fore.GREEN}✓ HTTP health check passed (200 OK)")
                    print(f"{Fore.WHITE}   Access at: http://{host}")
                    logger.info(f"Health check passed for {host} on attempt {attempt + 1}")
                    break
                elif attempt < self.health_retries - 1:
                    print(f"{Fore.YELLOW}⚠ Attempt {attempt + 1}/{self.health_retries} returned {status_code}, retrying...")
                    logger.warning(f"Health check attempt {attempt + 1} failed with {status_code}")
                else:
                    error_msg = f"Health check failed after {self.health_retries} attempts (status: {status_code})"
                    print(f"{Fore.RED}✗ {error_msg}")
                    logger.error(error_msg)
                    raise Exception(error_msg) 


            result = {
                'host': host,
                'status': 'success',
                'message': f'Deployed successfully - http://{host}'
            }
            
        except Exception as e:
            print(f"{Fore.RED}✗ Deployment failed: {e}")
            print(f"\n{Fore.YELLOW}{'─'*60}")
            print(f"{Fore.YELLOW}TROUBLESHOOTING STEPS:")
            print(f"{Fore.YELLOW}{'─'*60}")
            print(f"{Fore.WHITE}1. Check SSH connectivity:")
            print(f"{Fore.CYAN}   ssh -i terraform/deploy-key.pem ubuntu@{host}")
            print(f"{Fore.WHITE}2. Check deployment logs:")
            print(f"{Fore.CYAN}   cat scripts/logs/deployment_*.log | tail -50")
            print(f"{Fore.WHITE}3. Check Docker on remote host:")
            print(f"{Fore.CYAN}   ssh -i terraform/deploy-key.pem ubuntu@{host} 'docker ps'")
            print(f"{Fore.WHITE}4. Rollback if needed:")
            print(f"{Fore.CYAN}   python3 scripts/deploy.py --rollback")
            print(f"{Fore.YELLOW}{'─'*60}\n")
            logger.error(f"Deployment failed on {host}: {e}", exc_info=True)
            result = {
                'host': host,
                'status': 'failed',
                'message': str(e)
            }
        
        finally:
            if client:
                client.close()
                logger.debug(f"SSH connection to {host} closed")

        self.results.append(result)
        return result

    def rollback_deployment(self, host):
        """Rollback to previous deployment on a host"""
        print(f"\n{Fore.YELLOW}{'='*60}")
        print(f"{Fore.YELLOW}Rolling back deployment on {host}")
        print(f"{Fore.YELLOW}{'='*60}")
        
        client = None
        try:
            # Connect
            print(f"{Fore.YELLOW}→ Connecting to {host}...")
            client = self.connect_ssh(host)
            print(f"{Fore.GREEN}✓ Connected")
            
            remote_dir = self.remote_dir
            
            # Check if backup exists
            print(f"{Fore.YELLOW}→ Checking for previous version...")
            stdin, stdout, stderr = client.exec_command(f'test -d {remote_dir}.backup && echo "exists"')
            backup_exists = stdout.read().decode().strip() == "exists"
            
            if not backup_exists:
                print(f"{Fore.RED}✗ No backup found. Cannot rollback.")
                return {
                    'host': host,
                    'status': 'failed',
                    'message': 'No backup available'
                }
            
            # Stop current containers
            print(f"{Fore.YELLOW}→ Stopping current containers...")
            stdin, stdout, stderr = client.exec_command(
                f'cd {remote_dir} && docker compose down'
            )
            stdout.channel.recv_exit_status()
            print(f"{Fore.GREEN}✓ Containers stopped")
            
            # Restore from backup
            print(f"{Fore.YELLOW}→ Restoring previous version...")
            commands = [
                f'rm -rf {remote_dir}',
                f'mv {remote_dir}.backup {remote_dir}',
                f'cd {remote_dir} && docker compose up -d'
            ]
            
            for cmd in commands:
                stdin, stdout, stderr = client.exec_command(cmd)
                stdout.channel.recv_exit_status()
            
            print(f"{Fore.GREEN}✓ Previous version restored")
            
            # Verify
            print(f"{Fore.YELLOW}→ Verifying rollback...")
            time.sleep(2)
            stdin, stdout, stderr = client.exec_command(
                'curl -s -o /dev/null -w "%{http_code}" http://localhost'
            )
            status_code = stdout.read().decode().strip()
            
            if status_code == '200':
                print(f"{Fore.GREEN}✓ Rollback successful!")
                return {
                    'host': host,
                    'status': 'success',
                    'message': 'Rolled back successfully'
                }
            else:
                print(f"{Fore.RED}✗ Rollback verification failed")
                return {
                    'host': host,
                    'status': 'failed',
                    'message': f'HTTP returned {status_code}'
                }
        
        except Exception as e:
            print(f"{Fore.RED}✗ Rollback failed: {e}")
            return {
                'host': host,
                'status': 'failed',
                'message': str(e)
            }
        
        finally:
            if client:
                client.close()

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
    

    # Load configuration
    config_file = 'config.yaml'
    for arg in sys.argv:
        if arg.startswith('--config='):
            config_file = arg.split('=')[1]

    config = load_config(config_file)

    # Check for flags
    dry_run = '--dry-run' in sys.argv
    rollback = '--rollback' in sys.argv

    # Create deployment manager
    manager = DeploymentManager(key_path, service_path, config))
    
    # Load hosts
    hosts = manager.load_hosts(hosts_file)

    if rollback:
        print(f"{Fore.MAGENTA}{'='*60}")
        print(f"{Fore.MAGENTA}Docker Multi-Host ROLLBACK Script")
        print(f"{Fore.MAGENTA}{'='*60}")
        print(f"\n{Fore.WHITE}Rolling back {len(hosts)} hosts...")
        
        for host in hosts:
            manager.rollback_deployment(host)
        
        manager.print_summary()
        return  # Exit function - don't continue to deployment
    
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
