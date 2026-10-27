package bounds
import "testing"
func TestClamp(t *testing.T) {
 for _, c := range [][4]int{{5,0,10,5},{-1,0,10,0},{20,0,10,10},{3,3,3,3}} {
  if got := Clamp(c[0], c[1], c[2]); got != c[3] { t.Fatalf("Clamp%v = %v", c, got) }
 }
}
