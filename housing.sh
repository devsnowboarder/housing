#!/bin/bash

# Exit immediately if any command fails
set -e

# Navigate to the project directory
cd ~/python2026/housing

# Activate the virtual environment and run the script
source housing_env/bin/activate
python3 houseSearch.py

# Copy the HTML report to the remote server
scp peninsula_homes_for_sale.html fastsnow@67.20.113.100:/home2/fastsnow/public_html/housing/

