package main

import (
	"encoding/json"
	"errors"
	"net"
	"os"
	"strings"
	"time"
)

func boundaryProbe(pg, redis string) error {
	result := map[string]any{"nonroot": os.Getuid() != 0}
	_, err := os.OpenFile("/must-not-create", os.O_CREATE|os.O_WRONLY, 0600)
	result["root_readonly"] = err != nil
	data, err := os.ReadFile("/proc/self/status")
	if err != nil {
		return err
	}
	result["capabilities_dropped"] = strings.Contains(string(data), "CapEff:\t0000000000000000")
	for label, address := range map[string]string{"postgres_unreachable": pg, "redis_unreachable": redis} {
		conn, err := net.DialTimeout("tcp", address, time.Second)
		result[label] = err != nil
		if conn != nil {
			conn.Close()
		}
	}
	_, err = os.Stat("/var/run/docker.sock")
	result["docker_socket_absent"] = os.IsNotExist(err)
	for _, value := range result {
		if value != true {
			return errors.New("container boundary assertion failed")
		}
	}
	result["status"] = "PASS"
	return json.NewEncoder(os.Stdout).Encode(result)
}
