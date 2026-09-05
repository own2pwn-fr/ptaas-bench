// Stdlib only. The origin sits at the end of a proxy chain and has to accept
// messages the load balancer in front of it will forward but Go's own HTTP server
// refuses (see wire.go); pulling in a framework would put a second opinion about
// message framing in the path and we would be back where we started.
module edge.internal/origin

go 1.22
