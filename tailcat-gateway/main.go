// Host-owned bridge: identity comes from Tailcat's authenticated peer metadata.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"strings"
	"time"

	"github.com/tailscale/tailcat"
	"tailscale.com/net/netns"
	"tailscale.com/tailcfg"
	"tailscale.com/types/key"
)

const maxBody = 1100000

type Config struct {
	KeyFile      string             `json:"key_file"`
	Socket       string             `json:"socket"`
	EndpointFile string             `json:"endpoint_file"`
	Region       tailcfg.DERPRegion `json:"region"`
	Allowed      []string           `json:"allowed"`
}
type KeyFile struct {
	Private string `json:"private"`
	Public  string `json:"public"`
}

func readPrivate(path string) ([]byte, error) {
	st, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if st.Mode().Perm()&0077 != 0 {
		return nil, errors.New("private file permissions")
	}
	return os.ReadFile(path)
}
func privateKey(path string) (key.NodePrivate, error) {
	var data KeyFile
	raw, err := readPrivate(path)
	if err != nil {
		return key.NodePrivate{}, err
	}
	if err = json.Unmarshal(raw, &data); err != nil {
		return key.NodePrivate{}, err
	}
	var result key.NodePrivate
	err = result.UnmarshalText([]byte(data.Private))
	return result, err
}
func newKey(path string) error {
	k := key.NewNode()
	priv, _ := k.MarshalText()
	pub, _ := k.Public().MarshalText()
	data, _ := json.Marshal(KeyFile{string(priv), string(pub)})
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = file.Write(data)
	return err
}
func rpc(socket, peer string, body []byte) ([]byte, error) {
	return rpcWithTimeout(socket, peer, body, 6*time.Second)
}
func rpcWithTimeout(socket, peer string, body []byte, timeout time.Duration) ([]byte, error) {
	conn, err := net.DialTimeout("unix", socket, min(time.Second, timeout))
	if err != nil {
		return nil, errors.New("authority unavailable")
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(timeout))
	encoder := json.NewEncoder(conn)
	encoder.SetEscapeHTML(false)
	if err = encoder.Encode(map[string]string{"peer_key": peer, "body": string(body)}); err != nil {
		return nil, err
	}
	response, err := bufio.NewReader(io.LimitReader(conn, maxBody+1)).ReadBytes('\n')
	if len(response) > maxBody {
		return nil, errors.New("response capacity")
	}
	return response, err
}
func peerOf(server *tailcat.Server, conn net.Conn) (string, error) {
	for _, value := range server.PeerEnv(conn.LocalAddr(), conn.RemoteAddr()) {
		if strings.HasPrefix(value, "TAILCAT_PEER_KEY=nodekey:") {
			return strings.TrimPrefix(value, "TAILCAT_PEER_KEY="), nil
		}
	}
	return "", errors.New("unproven peer")
}
func handle(server *tailcat.Server, conn net.Conn, cfg Config) {
	defer conn.Close()
	peer, err := peerOf(server, conn)
	if err != nil {
		return
	}
	reader := bufio.NewReaderSize(conn, 65536)
	for {
		conn.SetDeadline(time.Now().Add(8 * time.Second))
		body, err := readLine(reader)
		if err != nil {
			return
		}
		response, err := rpc(cfg.Socket, peer, body)
		if err != nil {
			return
		}
		if _, err = conn.Write(response); err != nil {
			return
		}
	}
}
func readLine(reader *bufio.Reader) ([]byte, error) {
	var result []byte
	for {
		part, err := reader.ReadSlice('\n')
		if len(result)+len(part) > maxBody {
			return nil, errors.New("request capacity")
		}
		result = append(result, part...)
		if err == bufio.ErrBufferFull {
			continue
		}
		return result, err
	}
}
func serve(path string) error {
	raw, err := readPrivate(path)
	if err != nil {
		return err
	}
	var cfg Config
	if err = json.Unmarshal(raw, &cfg); err != nil {
		return err
	}
	if len(cfg.Allowed) == 0 || len(cfg.Allowed) > 32 || cfg.Region.RegionID == 0 || len(cfg.Region.Nodes) == 0 {
		return errors.New("explicit private relay and allowlist required")
	}
	priv, err := privateKey(cfg.KeyFile)
	if err != nil {
		return err
	}
	server := &tailcat.Server{Key: priv, Region: &cfg.Region, Logf: privateDiagnostic}
	for _, text := range cfg.Allowed {
		var peer key.NodePublic
		if err = peer.UnmarshalText([]byte(text)); err != nil {
			return err
		}
		server.AllowedClients = append(server.AllowedClients, peer)
	}
	listener, err := server.Listen(context.Background(), "tcp", ":8443")
	if err != nil {
		return err
	}
	defer server.Close()
	defer listener.Close()
	endpoint := []byte(server.TailcatAddr())
	file, err := os.OpenFile(cfg.EndpointFile, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	_, err = file.Write(endpoint)
	file.Close()
	if err != nil {
		return err
	}
	stopped := make(chan struct{})
	go supervise(server, listener, cfg, stopped)
	slots := make(chan struct{}, 16)
	for {
		conn, err := listener.Accept()
		if err != nil {
			close(stopped)
			return errors.New("transport held or closed")
		}
		select {
		case slots <- struct{}{}:
			go func() { defer func() { <-slots }(); handle(server, conn, cfg) }()
		default:
			conn.Close()
		}
	}
}
func supervise(server *tailcat.Server, listener net.Listener, cfg Config, stopped chan struct{}) {
	ticker := time.NewTicker(250 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-stopped:
			return
		case <-ticker.C:
			cycleDeadline := time.Now().Add(time.Second)
			for _, peer := range cfg.Allowed {
				body := []byte(`{"operation":"peer_check","arguments":{}}`)
				reply, err := rpcWithTimeout(cfg.Socket, peer, body, 500*time.Millisecond)
				var result struct {
					OK bool `json:"ok"`
				}
				if err == nil {
					err = json.Unmarshal(reply, &result)
				}
				if err != nil || !result.OK || time.Now().After(cycleDeadline) {
					listener.Close()
					server.Close()
					return
				}
			}
		}
	}
}
func send(keyPath, endpointPath string, stream bool) error {
	priv, err := privateKey(keyPath)
	if err != nil {
		return err
	}
	address, err := readPrivate(endpointPath)
	if err != nil {
		return err
	}
	client := tailcat.NewClient(tailcat.Addr(strings.TrimSpace(string(address))))
	client.Key = priv
	client.Logf = privateDiagnostic
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	conn, err := client.DialTCPPort(ctx, 8443)
	if err != nil {
		return errors.New("tailcat connection unavailable")
	}
	defer conn.Close()
	input := bufio.NewReader(os.Stdin)
	output := bufio.NewReader(conn)
	for {
		body, err := readLine(input)
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		conn.SetDeadline(time.Now().Add(6 * time.Second))
		if _, err = conn.Write(body); err != nil {
			return err
		}
		response, err := readLine(output)
		if err != nil {
			return errors.New("tailcat response unavailable")
		}
		if _, err = os.Stdout.Write(response); err != nil {
			return err
		}
		if !stream {
			break
		}
	}
	conn.Close()
	drainCtx, drainCancel := context.WithTimeout(context.Background(), time.Second)
	defer drainCancel()
	if err = client.DrainTCP(drainCtx); err != nil {
		return errors.New("tailcat close did not drain")
	}
	return nil
}
func main() {
	// This userspace bridge owns no TUN and uses ordinary container routes.
	netns.SetEnabled(false)
	var err error
	args := os.Args[1:]
	switch {
	case len(args) == 3 && args[0] == "boundary-probe":
		err = boundaryProbe(args[1], args[2])
	case len(args) == 2 && args[0] == "new-key":
		err = newKey(args[1])
	case len(args) == 2 && args[0] == "relay":
		err = relay(args[1])
	case len(args) == 2 && args[0] == "network-smoke":
		err = networkSmoke(args[1])
	case len(args) == 2 && args[0] == "serve":
		err = serve(args[1])
	case len(args) == 3 && args[0] == "session":
		err = send(args[1], args[2], true)
	case len(args) == 3 && args[0] == "send":
		// Bound the entire one-shot process, including any upstream cleanup stall.
		lifetime := time.AfterFunc(12*time.Second, func() { fmt.Fprintln(os.Stderr, "HOLD: native one-shot lifetime exceeded"); os.Exit(1) })
		defer lifetime.Stop()
		err = send(args[1], args[2], false)
	default:
		err = fmt.Errorf("usage: bridge new-key FILE | serve CONFIG | send KEY_FILE ENDPOINT_FILE")
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "HOLD:", err)
		os.Exit(1)
	}
}
