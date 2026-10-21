.PHONY: test vendor payload orm release

test:
	./provisioning/validate.sh

vendor:
	./provisioning/fetch-vendor.sh

payload:
	./provisioning/build-operator-payload.sh

orm:
	./provisioning/build-orm.sh

release:
	./provisioning/build-release.sh
