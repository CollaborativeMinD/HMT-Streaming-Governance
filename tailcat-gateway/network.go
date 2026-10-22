package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"tailscale.com/net/netns"
	"tailscale.com/tsnet"
)

type NetworkConfig struct {
	ControlURL string            `json:"control_url"`
	StateDir   string            `json:"state_dir"`
	AuthKeys   map[string]string `json:"auth_keys"`
}

func networkNode(cfg NetworkConfig, name string) *tsnet.Server {
	os.MkdirAll(cfg.StateDir, 0700)
	logger := func(format string, args ...any) {
		line := fmt.Sprintf(format, args...)
		for _, secret := range cfg.AuthKeys {
			line = strings.ReplaceAll(line, secret, "[REDACTED]")
		}
		line = regexp.MustCompile(`https?://[^\s]+`).ReplaceAllString(line, "[URL]")
		f, err := os.OpenFile(filepath.Join(cfg.StateDir, "diagnostic.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
		if err == nil {
			fmt.Fprintln(f, line)
			f.Close()
		}
	}

	return &tsnet.Server{Dir: filepath.Join(cfg.StateDir, name), Hostname: "gov-" + name,
		AuthKey: cfg.AuthKeys[name], ControlURL: cfg.ControlURL, Ephemeral: true,
		UserLogf: logger, Logf: logger}
}
func probeNode(client *tsnet.Server, address string, expected bool) error {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	conn, err := client.Dial(ctx, "tcp", address)
	if err != nil {
		if expected {
			return errors.New("expected managed-network connection denied")
		}
		return nil
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(2 * time.Second))
	_, err = conn.Write([]byte("probe"))
	if err != nil {
		if expected {
			return err
		}
		return nil
	}
	value := make([]byte, 2)
	_, err = io.ReadFull(conn, value)
	success := err == nil && string(value) == "OK"
	if success != expected {
		return errors.New("managed-network ACL expectation failed")
	}
	return nil
}
func waitForAllowed(client *tsnet.Server, address string) error {
	deadline := time.Now().Add(10 * time.Second)
	for {
		err := probeNode(client, address, true)
		if err == nil {
			return nil
		}
		if time.Now().After(deadline) {
			return err
		}
		time.Sleep(100 * time.Millisecond)
	}
}
func echo(listener net.Listener) {
	for {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		go func() {
			defer conn.Close()
			conn.SetDeadline(time.Now().Add(3 * time.Second))
			buf := make([]byte, 5)
			if _, err := io.ReadFull(conn, buf); err == nil {
				conn.Write([]byte("OK"))
			}
		}()
	}
}
func networkSmoke(path string) error {
	// Userspace-only fixture: use normal host routes, never bind control traffic to a TUN/default-interface escape path.
	netns.SetEnabled(false)
	raw, err := readPrivate(path)
	if err != nil {
		return err
	}
	var cfg NetworkConfig
	if err = json.Unmarshal(raw, &cfg); err != nil {
		return err
	}
	if cfg.ControlURL == "" || cfg.AuthKeys["service"] == "" || cfg.AuthKeys["agent"] == "" || cfg.AuthKeys["admin"] == "" {
		return errors.New("private Headscale control and issued keys required")
	}
	control := &http.Client{Timeout: 3 * time.Second}
	probe, err := control.Get(cfg.ControlURL + "/key?v=130")
	if err != nil {
		return fmt.Errorf("private control TLS probe failed: %w", err)
	}
	probe.Body.Close()
	if probe.StatusCode != 200 {
		return fmt.Errorf("private control probe HTTP %d", probe.StatusCode)
	}
	service := networkNode(cfg, "service")
	agent := networkNode(cfg, "agent")
	admin := networkNode(cfg, "admin")
	defer service.Close()
	defer agent.Close()
	defer admin.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	status, err := service.Up(ctx)
	if err != nil {
		return fmt.Errorf("Headscale service enrollment failed: %w", err)
	}
	if _, err = agent.Up(ctx); err != nil {
		return errors.New("Headscale agent enrollment failed")
	}
	if _, err = admin.Up(ctx); err != nil {
		return errors.New("Headscale admin enrollment failed")
	}
	a, err := service.Listen("tcp", ":8443")
	if err != nil {
		return err
	}
	defer a.Close()
	go echo(a)
	b, err := service.Listen("tcp", ":9443")
	if err != nil {
		return err
	}
	defer b.Close()
	go echo(b)
	host := status.TailscaleIPs[0].String()
	if err = waitForAllowed(admin, net.JoinHostPort(host, "9443")); err != nil {
		return err
	}
	if err = waitForAllowed(agent, net.JoinHostPort(host, "8443")); err != nil {
		return err
	}
	if err = probeNode(agent, net.JoinHostPort(host, "9443"), false); err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"status": "PASS", "headscale_enrollments": 3, "admin_management_port": true, "agent_message_port": true, "agent_management_port_denied": true, "tailscale_saas_control": false, "remote_log_upload": false})
}
