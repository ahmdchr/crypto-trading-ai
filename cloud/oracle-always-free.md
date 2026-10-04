# Oracle Cloud Always Free setup

Oracle's [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) include an Arm VM allowance of 2 OCPUs and 12 GiB of memory in your **home region**. A single `VM.Standard.A1.Flex` instance with 1 OCPU, 2 GiB RAM, and the default 50 GiB boot volume fits these limits. Use an Ubuntu image marked **Always Free Eligible**. This Python project uses only the standard library, so the Arm VM needs no architecture-specific packages.

## Create the instance

1. [Sign up for Oracle Cloud Free Tier](https://docs.oracle.com/en-us/iaas/Content/GSG/Tasks/signingup_topic-Sign_Up_for_Free_Oracle_Cloud_Promotion.htm). Oracle normally requires a payment card for verification, with a possible temporary hold, and says it will not charge the card unless you upgrade. Choose your home region carefully because Always Free compute must run there.
2. In the Oracle console, create a **Compute instance** in your home region. Choose an Always Free Eligible Ubuntu image and `VM.Standard.A1.Flex` with **1 OCPU, 2 GiB RAM**, and the default **50 GiB boot volume**. Confirm the final configuration is marked Always Free Eligible. [Oracle's instance creation guide](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm) describes the current console flow.
3. Assign a public IP and add your SSH public key. Allow inbound TCP 22 only from your IP address. The bot needs outbound HTTPS; it does not serve a website or need inbound HTTP ports.
4. Connect using `ssh ubuntu@PUBLIC_IP` with the matching private key kept on your computer. Check `python3 --version`; use an Ubuntu release with Python 3.10 or newer.

If Oracle reports **out of host capacity**, try another availability domain in the same home region or retry later. Oracle [may reclaim idle Always Free VMs](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm#Always_Free_Resources-Infrastructure); this bot's light workload might qualify, so check the instance and bot periodically. This option has no guaranteed continuous uptime.

## Upload and switch over

From the project directory on your computer, replace `PUBLIC_IP` below with the VM's public IP:

```bash
tar -czf /tmp/crypto-paper-code.tgz paper_bot.py trader.py learning.py status.py cloud
scp /tmp/crypto-paper-code.tgz ubuntu@PUBLIC_IP:/tmp/
ssh ubuntu@PUBLIC_IP 'mkdir -p ~/crypto-trading-ai && tar -xzf /tmp/crypto-paper-code.tgz -C ~/crypto-trading-ai'
```

Once code is uploaded and SSH works, follow the [wallet transfer and cutover steps](README.md#transfer-the-existing-paper-wallet). Use `scp /tmp/crypto-paper-wallet.sqlite3 ubuntu@PUBLIC_IP:/tmp/` to transfer the snapshot, then on the VM run:

```bash
sudo bash ~/crypto-trading-ai/cloud/bootstrap-ubuntu.sh /tmp/crypto-paper-wallet.sqlite3
sudo -u crypto-paper python3 /opt/crypto-trading-ai/status.py --db /var/lib/crypto-paper-bot/paper.sqlite3
```

Keep the local service stopped during cutover. After the cloud service and wallet are confirmed, disable local automatic startup. If the VM is reclaimed later, preserve or restore the latest cloud wallet backup before restarting elsewhere so the paper trade history stays continuous.
