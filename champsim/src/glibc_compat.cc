#include <cstdlib>

// Static libraries copied from newer Linux distributions may reference glibc
// 2.38 C23 entry points. Ubuntu 22.04 does not provide them, so keep the
// existing C++ build linkable on AutoDL images by forwarding to the classic
// conversion routines. CLI11 only needs integer option parsing here.
extern "C" long __isoc23_strtol(const char* nptr, char** endptr, int base)
{
  return std::strtol(nptr, endptr, base);
}

extern "C" unsigned long __isoc23_strtoul(const char* nptr, char** endptr, int base)
{
  return std::strtoul(nptr, endptr, base);
}

extern "C" long long __isoc23_strtoll(const char* nptr, char** endptr, int base)
{
  return std::strtoll(nptr, endptr, base);
}

extern "C" unsigned long long __isoc23_strtoull(const char* nptr, char** endptr, int base)
{
  return std::strtoull(nptr, endptr, base);
}
