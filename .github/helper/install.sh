#!/bin/bash

set -e

cd ~ || exit

sudo apt update && sudo apt install redis-server libcups2-dev

pip install frappe-bench

frappeuser=${FRAPPE_USER:-"frappe"}
frappebranch=${FRAPPE_BRANCH:-${GITHUB_BASE_REF:-${GITHUB_REF##*/}}}

git clone "https://github.com/${frappeuser}/frappe" --branch "${frappebranch}" --depth 1
bench init --skip-assets --frappe-path ~/frappe --python "$(which python)" frappe-bench

mkdir ~/frappe-bench/sites/test_site

if [ "$DB" == "mariadb" ];then
    cp -r "${GITHUB_WORKSPACE}/.github/helper/site_config_mariadb.json" ~/frappe-bench/sites/test_site/site_config.json
else
    cp -r "${GITHUB_WORKSPACE}/.github/helper/site_config_postgres.json" ~/frappe-bench/sites/test_site/site_config.json
fi


if [ "$DB" == "mariadb" ];then
    mysql --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL character_set_server = 'utf8mb4'"
    mysql --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL collation_server = 'utf8mb4_unicode_ci'"

    mysql --host 127.0.0.1 --port 3306 -u root -proot -e "CREATE USER 'test_frappe'@'localhost' IDENTIFIED BY 'test_frappe'"
    mysql --host 127.0.0.1 --port 3306 -u root -proot -e "CREATE DATABASE test_frappe"
    mysql --host 127.0.0.1 --port 3306 -u root -proot -e "GRANT ALL PRIVILEGES ON \`test_frappe\`.* TO 'test_frappe'@'localhost'"

    mysql --host 127.0.0.1 --port 3306 -u root -proot -e "FLUSH PRIVILEGES"
fi

if [ "$DB" == "postgres" ];then
    echo "travis" | psql -h 127.0.0.1 -p 5432 -c "CREATE DATABASE test_frappe" -U postgres;
    echo "travis" | psql -h 127.0.0.1 -p 5432 -c "CREATE USER test_frappe WITH PASSWORD 'test_frappe'" -U postgres;
fi


install_whktml() {
    if [ "$(lsb_release -rs)" = "22.04" ]; then
        wget -O /tmp/wkhtmltox.deb https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.jammy_amd64.deb
        sudo apt install /tmp/wkhtmltox.deb
    else
        echo "Please update this script to support wkhtmltopdf for $(lsb_release -ds)"
        exit 1
    fi
}
install_whktml &
wkpid=$!


cd ~/frappe-bench || exit

sed -i 's/watch:/# watch:/g' Procfile
sed -i 's/schedule:/# schedule:/g' Procfile
sed -i 's/socketio:/# socketio:/g' Procfile
sed -i 's/redis_socketio:/# redis_socketio:/g' Procfile

bench get-app payments
bench get-app erpnext "${GITHUB_WORKSPACE}"

if [ "$TYPE" == "server" ]; then bench setup requirements --dev; fi

wait $wkpid

bench start &> bench_run_logs.txt &
CI=Yes bench build --app frappe &
bench --site test_site reinstall --yes
