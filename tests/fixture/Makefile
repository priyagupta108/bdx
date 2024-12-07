FILES := \
	 subdir/bar.cpp \
	 subdir/foo.c \
	 toplev.c \

OBJFILES := $(patsubst %,%.o,${FILES})

%.c.o: %.c
	${CC} ${CFLAGS} -c -o $@ $^

%.cpp.o: %.cpp
	${CXX} ${CFLAGS} -c -o $@ $^

default: ${OBJFILES}

clean:
	rm -f ${OBJFILES}

.PHONY: default clean
